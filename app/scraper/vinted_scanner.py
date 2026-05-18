"""
vinted_scanner.py — Fetch Vinted.it e salvataggio annunci grezzi.

Stesso ruolo di scanner.py ma per Vinted.it.
Si appoggia alla stessa tabella scan_results (url UNIQUE → dedup automatico).

Endpoint confermato: GET /api/v2/catalog/items
User API:           GET /api/v2/users/{id}  (paese + città)
Auth: cookie session (access_token_web) ottenuto visitando la home.
Nessun ScraperAPI necessario — l'IP di Railway passa Datadome direttamente.

Differenze rispetto a scanner.py:
- price è un oggetto {"amount": "550.0", "currency_code": "EUR"}
- total_item_price = prezzo reale per l'acquirente (price + fee ~5%+€0.70)
- location = "{city}, {country_title}" estratta da /api/v2/users/{id}
- country = country_code ISO (es. "IT", "ES", "FR") — colonna nuova in scan_results
- source = 'Vinted.it'
- image_url = URL foto principale (colonna nuova in scan_results)
- Nessun campo body (non esposto dalla search API)

Prerequisiti DB:
    ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS country text DEFAULT NULL;
    ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS image_url text DEFAULT NULL;
- Nessun campo body (non esposto dalla search API)

Prerequisito DB:
    ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS country text DEFAULT NULL;
"""

import os
import re
import json
import time
import asyncio
import requests
from supabase import create_client, Client

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

VINTED_HOME        = "https://www.vinted.it"
VINTED_SEARCH_API  = f"{VINTED_HOME}/api/v2/catalog/items"
VINTED_USER_API    = f"{VINTED_HOME}/api/v2/users"

CATALOG_FOTOGRAFIA  = 3848
CATALOG_ELETTRONICA = 2994

HEADERS_HOME = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

HEADERS_API = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": "https://www.vinted.it/catalog",
    "X-Requested-With": "XMLHttpRequest",
}


# ──────────────────────────────────────────────
# Supabase
# ──────────────────────────────────────────────

def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ──────────────────────────────────────────────
# Vinted session
# ──────────────────────────────────────────────

def _get_vinted_session() -> requests.Session:
    """
    Visita la home per ottenere access_token_web dai cookie.
    JWT valido ~2h — sufficiente per un ciclo di scan completo.
    """
    session = requests.Session()
    session.headers.update(HEADERS_HOME)
    r = session.get(VINTED_HOME, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Vinted home returned {r.status_code}")
    session.headers.update(HEADERS_API)
    return session


# ──────────────────────────────────────────────
# Parsing prezzi
# ──────────────────────────────────────────────

def _parse_price(price_obj) -> tuple[str, float | None]:
    """
    Vinted price: {"amount": "550.0", "currency_code": "EUR"}
    Ritorna (price_raw, price_value).
    """
    if isinstance(price_obj, dict):
        try:
            amount = float(price_obj.get("amount", 0))
            currency = price_obj.get("currency_code", "EUR")
            return f"{amount:.2f} {currency}", amount
        except (ValueError, TypeError):
            pass
    return "N/D", None


# ──────────────────────────────────────────────
# User API — paese e città
# ──────────────────────────────────────────────

def _fetch_user_details(session: requests.Session, user_ids: list[int]) -> dict[int, dict]:
    """
    Chiama /api/v2/users/{id} per ogni user_id univoco.
    Ritorna mapping user_id → {country_code, location}.

    Chiamata solo per gli item che hanno già passato i filtri
    (keyword nel titolo + price_value presente) — riduce al minimo le call.

    Campi estratti dalla user API (verificati sul raw):
        user.country_code       → "IT", "ES", "FR" ...
        user.country_title      → "Italia", "Spagna" ... (in italiano)
        user.city               → "Milano", "Sant Antoni de Portmany" ...
        user.expose_location    → bool (se False la città potrebbe mancare)
    """
    result = {}
    for uid in user_ids:
        try:
            r = session.get(f"{VINTED_USER_API}/{uid}", timeout=15)
            if r.status_code != 200:
                result[uid] = {"country_code": None, "location": ""}
                continue

            user = r.json().get("user", {})
            country_code  = user.get("country_code") or user.get("country_iso_code")
            country_title = user.get("country_title", "")
            city          = user.get("city", "") if user.get("expose_location") else ""

            if city and country_title:
                location = f"{city}, {country_title}"
            elif country_title:
                location = country_title
            else:
                location = ""

            result[uid] = {
                "country_code": country_code,  # es. "IT", "ES"
                "location": location,           # es. "Milano, Italia"
            }
        except Exception:
            result[uid] = {"country_code": None, "location": ""}

        time.sleep(0.3)  # gentile verso Vinted

    return result


# ──────────────────────────────────────────────
# Item API — descrizione annuncio
# ──────────────────────────────────────────────

def _fetch_item_descriptions(session: requests.Session, items: list[dict]) -> dict[int, str]:
    """
    Fetcha la pagina HTML di ogni annuncio nuovo ed estrae la descrizione.
    Ritorna mapping item_id -> description (str).

    Vinted non espone la descrizione nella search API ne tramite /api/v2/items/{id}.
    La descrizione e embedded nel HTML come stringa JSON — estratta con
    json.JSONDecoder().raw_decode() che gestisce correttamente qualsiasi
    lunghezza, escape sequence e carattere unicode.

    Chiamata SOLO per i nuovi annunci (~2MB/pagina).
    """
    HEADERS_HTML = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9",
    }
    KEY = '"description":'
    decoder = json.JSONDecoder()

    result = {}
    for item in items:
        iid = item.get("id")
        url = item.get("url", "")
        if not iid or not url:
            if iid:
                result[iid] = ""
            continue
        try:
            r = session.get(url, headers=HEADERS_HTML, timeout=25)
            if r.status_code != 200:
                result[iid] = ""
                continue

            html = r.text
            idx = html.find(KEY)
            if idx == -1:
                result[iid] = ""
                continue

            # Trova il primo " dopo i due punti (salta eventuali spazi)
            val_start = idx + len(KEY)
            while val_start < len(html) and html[val_start] in " \t\n\r":
                val_start += 1

            if val_start >= len(html) or html[val_start] != '"':
                result[iid] = ""
                continue

            # raw_decode parsa la stringa JSON completa dalla posizione val_start
            value, _ = decoder.raw_decode(html, val_start)
            result[iid] = value if isinstance(value, str) else ""

        except Exception:
            result[iid] = ""
        time.sleep(0.5)

    return result


"""
vinted_scanner.py — Fetch Vinted.it e salvataggio annunci grezzi.

Stesso ruolo di scanner.py ma per Vinted.it.
Si appoggia alla stessa tabella scan_results (url UNIQUE → dedup automatico).

Endpoint confermato: GET /api/v2/catalog/items
User API:           GET /api/v2/users/{id}  (paese + città)
Auth: cookie session (access_token_web) ottenuto visitando la home.
Nessun ScraperAPI necessario — l'IP di Railway passa Datadome direttamente.

Differenze rispetto a scanner.py:
- price è un oggetto {"amount": "550.0", "currency_code": "EUR"}
- total_item_price = prezzo reale per l'acquirente (price + fee ~5%+€0.70)
- location = "{city}, {country_title}" estratta da /api/v2/users/{id}
- country = country_code ISO (es. "IT", "ES", "FR") — colonna nuova in scan_results
- source = 'Vinted.it'
- image_url = URL foto principale (colonna nuova in scan_results)
- Nessun campo body (non esposto dalla search API)

Prerequisiti DB:
    ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS country text DEFAULT NULL;
    ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS image_url text DEFAULT NULL;
- Nessun campo body (non esposto dalla search API)

Prerequisito DB:
    ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS country text DEFAULT NULL;
"""

import os
import re
import json
import time
import asyncio
import requests
from supabase import create_client, Client

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

VINTED_HOME        = "https://www.vinted.it"
VINTED_SEARCH_API  = f"{VINTED_HOME}/api/v2/catalog/items"
VINTED_USER_API    = f"{VINTED_HOME}/api/v2/users"

CATALOG_FOTOGRAFIA  = 3848
CATALOG_ELETTRONICA = 2994

HEADERS_HOME = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

HEADERS_API = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": "https://www.vinted.it/catalog",
    "X-Requested-With": "XMLHttpRequest",
}


# ──────────────────────────────────────────────
# Supabase
# ──────────────────────────────────────────────

def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ──────────────────────────────────────────────
# Vinted session
# ──────────────────────────────────────────────

def _get_vinted_session() -> requests.Session:
    """
    Visita la home per ottenere access_token_web dai cookie.
    JWT valido ~2h — sufficiente per un ciclo di scan completo.
    """
    session = requests.Session()
    session.headers.update(HEADERS_HOME)
    r = session.get(VINTED_HOME, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Vinted home returned {r.status_code}")
    session.headers.update(HEADERS_API)
    return session


# ──────────────────────────────────────────────
# Parsing prezzi
# ──────────────────────────────────────────────

def _parse_price(price_obj) -> tuple[str, float | None]:
    """
    Vinted price: {"amount": "550.0", "currency_code": "EUR"}
    Ritorna (price_raw, price_value).
    """
    if isinstance(price_obj, dict):
        try:
            amount = float(price_obj.get("amount", 0))
            currency = price_obj.get("currency_code", "EUR")
            return f"{amount:.2f} {currency}", amount
        except (ValueError, TypeError):
            pass
    return "N/D", None


# ──────────────────────────────────────────────
# User API — paese e città
# ──────────────────────────────────────────────

def _fetch_user_details(session: requests.Session, user_ids: list[int]) -> dict[int, dict]:
    """
    Chiama /api/v2/users/{id} per ogni user_id univoco.
    Ritorna mapping user_id → {country_code, location}.

    Chiamata solo per gli item che hanno già passato i filtri
    (keyword nel titolo + price_value presente) — riduce al minimo le call.

    Campi estratti dalla user API (verificati sul raw):
        user.country_code       → "IT", "ES", "FR" ...
        user.country_title      → "Italia", "Spagna" ... (in italiano)
        user.city               → "Milano", "Sant Antoni de Portmany" ...
        user.expose_location    → bool (se False la città potrebbe mancare)
    """
    result = {}
    for uid in user_ids:
        try:
            r = session.get(f"{VINTED_USER_API}/{uid}", timeout=15)
            if r.status_code != 200:
                result[uid] = {"country_code": None, "location": ""}
                continue

            user = r.json().get("user", {})
            country_code  = user.get("country_code") or user.get("country_iso_code")
            country_title = user.get("country_title", "")
            city          = user.get("city", "") if user.get("expose_location") else ""

            if city and country_title:
                location = f"{city}, {country_title}"
            elif country_title:
                location = country_title
            else:
                location = ""

            result[uid] = {
                "country_code": country_code,  # es. "IT", "ES"
                "location": location,           # es. "Milano, Italia"
            }
        except Exception:
            result[uid] = {"country_code": None, "location": ""}

        time.sleep(0.3)  # gentile verso Vinted

    return result


# ──────────────────────────────────────────────
# Item API — descrizione annuncio
"""
vinted_scanner.py — Fetch Vinted.it e salvataggio annunci grezzi.

Stesso ruolo di scanner.py ma per Vinted.it.
Si appoggia alla stessa tabella scan_results (url UNIQUE → dedup automatico).

Endpoint confermato: GET /api/v2/catalog/items
User API:           GET /api/v2/users/{id}  (paese + città)
Auth: cookie session (access_token_web) ottenuto visitando la home.
Nessun ScraperAPI necessario — l'IP di Railway passa Datadome direttamente.

Differenze rispetto a scanner.py:
- price è un oggetto {"amount": "550.0", "currency_code": "EUR"}
- total_item_price = prezzo reale per l'acquirente (price + fee ~5%+€0.70)
- location = "{city}, {country_title}" estratta da /api/v2/users/{id}
- country = country_code ISO (es. "IT", "ES", "FR") — colonna nuova in scan_results
- source = 'Vinted.it'
- image_url = URL foto principale (colonna nuova in scan_results)
- Nessun campo body (non esposto dalla search API)

Prerequisiti DB:
    ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS country text DEFAULT NULL;
    ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS image_url text DEFAULT NULL;
- Nessun campo body (non esposto dalla search API)

Prerequisito DB:
    ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS country text DEFAULT NULL;
"""

import os
import re
import json
import time
import asyncio
import requests
from supabase import create_client, Client

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

VINTED_HOME        = "https://www.vinted.it"
VINTED_SEARCH_API  = f"{VINTED_HOME}/api/v2/catalog/items"
VINTED_USER_API    = f"{VINTED_HOME}/api/v2/users"

CATALOG_FOTOGRAFIA  = 3848
CATALOG_ELETTRONICA = 2994

HEADERS_HOME = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

HEADERS_API = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": "https://www.vinted.it/catalog",
    "X-Requested-With": "XMLHttpRequest",
}


# ──────────────────────────────────────────────
# Supabase
# ──────────────────────────────────────────────

def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ──────────────────────────────────────────────
# Vinted session
# ──────────────────────────────────────────────

def _get_vinted_session() -> requests.Session:
    """
    Visita la home per ottenere access_token_web dai cookie.
    JWT valido ~2h — sufficiente per un ciclo di scan completo.
    """
    session = requests.Session()
    session.headers.update(HEADERS_HOME)
    r = session.get(VINTED_HOME, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Vinted home returned {r.status_code}")
    session.headers.update(HEADERS_API)
    return session


# ──────────────────────────────────────────────
# Parsing prezzi
# ──────────────────────────────────────────────

def _parse_price(price_obj) -> tuple[str, float | None]:
    """
    Vinted price: {"amount": "550.0", "currency_code": "EUR"}
    Ritorna (price_raw, price_value).
    """
    if isinstance(price_obj, dict):
        try:
            amount = float(price_obj.get("amount", 0))
            currency = price_obj.get("currency_code", "EUR")
            return f"{amount:.2f} {currency}", amount
        except (ValueError, TypeError):
            pass
    return "N/D", None


# ──────────────────────────────────────────────
# User API — paese e città
# ──────────────────────────────────────────────

def _fetch_user_details(session: requests.Session, user_ids: list[int]) -> dict[int, dict]:
    """
    Chiama /api/v2/users/{id} per ogni user_id univoco.
    Ritorna mapping user_id → {country_code, location}.

    Chiamata solo per gli item che hanno già passato i filtri
    (keyword nel titolo + price_value presente) — riduce al minimo le call.

    Campi estratti dalla user API (verificati sul raw):
        user.country_code       → "IT", "ES", "FR" ...
        user.country_title      → "Italia", "Spagna" ... (in italiano)
        user.city               → "Milano", "Sant Antoni de Portmany" ...
        user.expose_location    → bool (se False la città potrebbe mancare)
    """
    result = {}
    for uid in user_ids:
        try:
            r = session.get(f"{VINTED_USER_API}/{uid}", timeout=15)
            if r.status_code != 200:
                result[uid] = {"country_code": None, "location": ""}
                continue

            user = r.json().get("user", {})
            country_code  = user.get("country_code") or user.get("country_iso_code")
            country_title = user.get("country_title", "")
            city          = user.get("city", "") if user.get("expose_location") else ""

            if city and country_title:
                location = f"{city}, {country_title}"
            elif country_title:
                location = country_title
            else:
                location = ""

            result[uid] = {
                "country_code": country_code,  # es. "IT", "ES"
                "location": location,           # es. "Milano, Italia"
            }
        except Exception:
            result[uid] = {"country_code": None, "location": ""}

        time.sleep(0.3)  # gentile verso Vinted

    return result

# ──────────────────────────────────────────────
# Filtri
# ──────────────────────────────────────────────

def _keyword_in_title(keyword: str, title: str) -> bool:
    """Tutti i token della keyword devono apparire nel titolo."""
    if not title:
        return False
    title_lower = title.lower()
    return all(token in title_lower for token in keyword.lower().split())


def _catalog_id_for_keyword(keyword: str) -> int | None:
    """Catalog_id Vinted in base alla keyword."""
    foto_hints = {"canon", "nikon", "leica", "olympus", "obiettivo",
                  "fotocamera", "tamron", "fujifilm", "lumix"}
    tech_hints = {"iphone", "thinkpad", "samsung", "macbook",
                  "ipad", "console", "playstation", "nintendo"}
    kw = keyword.lower()
    if any(h in kw for h in foto_hints):
        return CATALOG_FOTOGRAFIA
    if any(h in kw for h in tech_hints):
        return CATALOG_ELETTRONICA
    return None


# ──────────────────────────────────────────────
# Fetch Vinted search
# ──────────────────────────────────────────────

def _fetch_vinted(session: requests.Session, keyword: str, per_page: int = 96) -> list[dict]:
    params = {
        "page": 1,
        "per_page": per_page,
        "search_text": keyword,
        "order": "newest_first",
        "time": int(time.time()),
    }
    catalog_id = _catalog_id_for_keyword(keyword)
    if catalog_id is not None:
        params["catalog_ids"] = catalog_id

    r = session.get(VINTED_SEARCH_API, params=params, timeout=20)
    if r.status_code != 200:
        raise ValueError(f"Vinted search API returned {r.status_code}")
    return r.json().get("items", [])


# ──────────────────────────────────────────────
# Scan per keyword
# ──────────────────────────────────────────────

def _scan_vinted_keyword(session: requests.Session, keyword: str) -> dict:
    """
    Fetch Vinted + filtri + salvataggio annunci completi.

    Un annuncio viene salvato solo se ha tutti i campi obbligatori:
    - keyword nel titolo
    - price_value presente
    - country (da user API) — skip se profilo venditore incompleto al momento dello scan
    - body (da HTML pagina) — obbligatorio su Vinted, assente = timing issue

    Gli annunci incompleti vengono scartati: ricompariranno al ciclo successivo
    come truly_new e verranno salvati correttamente una volta che il profilo è completo.
    """
    supabase = _get_supabase()

    # ── Fetch annunci ──
    try:
        raw_items = _fetch_vinted(session, keyword)
    except Exception as e:
        return {"keyword": keyword, "error": str(e),
                "found": 0, "new": 0, "updated": 0, "skipped": 0,
                "rejected": 0, "incomplete": 0}

    if not raw_items:
        return {"keyword": keyword, "found": 0, "new": 0,
                "updated": 0, "skipped": 0, "rejected": 0, "incomplete": 0}

    # ── URL già in DB ──
    existing_response = (
        supabase.table("scan_results")
        .select("url, price_value")
        .eq("keyword", keyword)
        .eq("source", "Vinted.it")
        .execute()
    )
    existing = {row["url"]: row["price_value"] for row in (existing_response.data or [])}

    # ── Pre-filtro: keyword nel titolo + price_value presente ──
    candidates = []
    rejected   = 0
    for item in raw_items:
        if not item.get("url"):
            continue
        if not _keyword_in_title(keyword, item.get("title", "")):
            rejected += 1
            continue
        _, price_value = _parse_price(item.get("price"))
        if not price_value:
            continue
        candidates.append(item)

    truly_new = [c for c in candidates if c.get("url") not in existing]
    in_db     = [c for c in candidates if c.get("url") in existing]

    # ── Fetch user details e descrizioni (solo per i nuovi) ──
    user_ids = list({
        item["user"]["id"]
        for item in truly_new
        if isinstance(item.get("user"), dict) and item["user"].get("id")
    })
    user_details = _fetch_user_details(session, user_ids) if user_ids else {}

    new_items_list = [c for c in truly_new if c.get("id") and c.get("url")]
    item_descriptions = _fetch_item_descriptions(session, new_items_list) if new_items_list else {}

    # ── Nuovi annunci ──
    new_rows   = []
    incomplete = 0
    for item in truly_new:
        url   = item.get("url", "")
        title = item.get("title", "")

        price_raw, price_value = _parse_price(item.get("price"))
        _, total_price = _parse_price(item.get("total_item_price"))
        _, service_fee = _parse_price(item.get("service_fee"))

        price_raw_full = (
            f"{total_price:.2f} EUR (prodotto: {price_value:.2f} EUR + fee: {service_fee:.2f} EUR)"
            if total_price and service_fee else price_raw
        )

        uid       = item.get("user", {}).get("id") if isinstance(item.get("user"), dict) else None
        user_info = user_details.get(uid, {}) if uid else {}
        country   = user_info.get("country_code")
        location  = user_info.get("location", "")
        body      = item_descriptions.get(item.get("id"), "") or ""
        condition = item.get("status", "non specificata") or "non specificata"

        # Skip se country o body mancanti (profilo/annuncio incompleto)
        if not country or not body:
            incomplete += 1
            continue

        new_rows.append({
            "keyword":    keyword,
            "title":      title,
            "price_raw":  price_raw_full,
            "price_value": price_value,
            "location":   location,
            "country":    country,
            "url":        url,
            "date_listed": None,
            "source":     "Vinted.it",
            "scored":     False,
            "condition":  condition,
            "image_url":  item.get("photo", {}).get("url") or None,
            "body":       body,
        })

    if new_rows:
        supabase.table("scan_results").upsert(
            new_rows, on_conflict="url", ignore_duplicates=True
        ).execute()

    # ── Price drop check per annunci già in DB ──
    skipped = 0
    updated = 0
    for item in in_db:
        url = item.get("url", "")

        price_raw, price_value = _parse_price(item.get("price"))
        _, total_price = _parse_price(item.get("total_item_price"))
        _, service_fee = _parse_price(item.get("service_fee"))

        price_raw_full = (
            f"{total_price:.2f} EUR (prodotto: {price_value:.2f} EUR + fee: {service_fee:.2f} EUR)"
            if total_price and service_fee else price_raw
        )

        old_price = existing[url]
        if old_price == price_value:
            skipped += 1
            continue

        if old_price and (old_price - price_value) / old_price >= 0.15:
            supabase.table("scan_results").update({
                "price_raw":  price_raw_full,
                "price_value": price_value,
                "scored":     False,
                "score":      None,
                "margine_stimato": None,
                "motivazione": None,
                "rischi":     None,
            }).eq("url", url).execute()
            scan_ids = (
                supabase.table("scan_results")
                .select("id")
                .eq("url", url)
                .execute()
            )
            if scan_ids.data:
                supabase.table("notifications_log").delete().eq(
                    "scan_result_id", scan_ids.data[0]["id"]
                ).execute()
            updated += 1
        else:
            supabase.table("scan_results").update({
                "price_raw":  price_raw_full,
                "price_value": price_value,
            }).eq("url", url).execute()
            skipped += 1

    return {
        "keyword":       keyword,
        "found":         len(raw_items),
        "new":           len(new_rows),
        "updated":       updated,
        "skipped":       skipped,
        "rejected":      rejected,
        "incomplete":    incomplete,
        "user_api_calls": len(user_ids),
        "item_api_calls": len(new_items_list),
    }



# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

async def run_vinted_scan() -> dict:
    """
    Cron job: fetch Vinted per ogni keyword attiva, salva annunci grezzi.
    Pattern identico a run_scan_and_save() in scanner.py.
    """
    try:
        session = await asyncio.to_thread(_get_vinted_session)
    except Exception as e:
        return {"status": "error", "detail": f"Vinted session failed: {e}"}

    try:
        supabase = _get_supabase()
        kw_response = (
            supabase.table("keywords")
            .select("keyword")
            .eq("active", True)
            .execute()
        )
        keywords = [row["keyword"].lower() for row in (kw_response.data or [])]
    except Exception as e:
        return {"status": "error", "detail": f"Keywords fetch failed: {e}"}

    if not keywords:
        return {"status": "ok", "message": "Nessuna keyword attiva"}

    results = []
    for i, keyword in enumerate(keywords):
        if i > 0:
            await asyncio.sleep(3)
        result = await asyncio.to_thread(_scan_vinted_keyword, session, keyword)
        results.append(result)

    return {
        "status": "ok",
        "source": "Vinted.it",
        "keywords_scanned": len(keywords),
        "total_new": sum(r.get("new", 0) for r in results),
        "total_user_api_calls": sum(r.get("user_api_calls", 0) for r in results),
        "total_item_api_calls": sum(r.get("item_api_calls", 0) for r in results),
        "results": results,
    }
