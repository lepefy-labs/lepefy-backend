"""
vinted_scanner.py — Fetch Vinted.it e salvataggio annunci grezzi.

Stesso ruolo di scanner.py ma per Vinted.it.
Si appoggia alla stessa tabella scan_results (url UNIQUE → dedup automatico).

Endpoint confermato: GET /api/v2/catalog/items
Auth: cookie session (access_token_web) ottenuto visitando la home.
Nessun ScraperAPI necessario — l'IP di Railway passa Datadome direttamente.

Differenze rispetto a scanner.py:
- Fetch via requests.Session con cookie (no ScraperAPI)
- price è un oggetto {"amount": "550.0", "currency_code": "EUR"}
- total_item_price = prezzo reale per l'acquirente (price + fee ~5%+€0.70)
- Filtro lingua: scarta listing con titoli chiaramente non italiani
- source = 'Vinted.it'
- Nessun campo body (Vinted non lo espone nella search API)
"""

import os
import re
import time
import asyncio
import requests
from supabase import create_client, Client

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

VINTED_HOME = "https://www.vinted.it"
VINTED_API  = f"{VINTED_HOME}/api/v2/catalog/items"

# Catalog IDs vinted.it — filtro categoria per ridurre rumore
CATALOG_FOTOGRAFIA  = 3848
CATALOG_ELETTRONICA = 2994

# Parole frequenti in francese/portoghese/spagnolo/tedesco.
# Vinted.it è cross-border: listing europei appaiono sul dominio .it.
# Se il titolo contiene > 2 di questi indicatori, lo scartiamo.
_NON_IT_RE = re.compile(
    r"\b(le|la|les|un|une|des|du|pour|avec|dans|très|neuf|taille|vendu"
    r"|novo|nova|para|com|sem|usado"
    r"|nuevo|nueva|con|sin|usado|usada|talla|vendo"
    r"|neu|für|mit|aus|sehr|guter|gute)\b",
    re.IGNORECASE,
)

HEADERS_HOME = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

HEADERS_API = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
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
    Visita la home di Vinted per ottenere access_token_web dai cookie.
    Il token è un JWT valido ~2h — sufficiente per un singolo ciclo di scan.
    Solleva RuntimeError se la home non risponde 200.
    """
    session = requests.Session()
    session.headers.update(HEADERS_HOME)
    r = session.get(VINTED_HOME, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Vinted home returned {r.status_code}")
    session.headers.update(HEADERS_API)
    return session


# ──────────────────────────────────────────────
# Parsing
# ──────────────────────────────────────────────

def _parse_price(price_obj) -> tuple[str, float | None]:
    """
    Vinted restituisce price come {"amount": "550.0", "currency_code": "EUR"}.
    Ritorna (price_raw: str, price_value: float | None).
    """
    if isinstance(price_obj, dict):
        try:
            amount = float(price_obj.get("amount", 0))
            currency = price_obj.get("currency_code", "EUR")
            return f"{amount:.2f} {currency}", amount
        except (ValueError, TypeError):
            return "N/D", None
    return "N/D", None


def _is_likely_italian(title: str) -> bool:
    """
    Filtra listing con titoli chiaramente non italiani.
    Se > 2 indicatori di lingue straniere → scarta.
    """
    if not title:
        return True
    return len(_NON_IT_RE.findall(title)) <= 2


def _keyword_in_title(keyword: str, title: str) -> bool:
    """Tutti i token della keyword devono apparire nel titolo (case-insensitive)."""
    if not title:
        return False
    title_lower = title.lower()
    return all(token in title_lower for token in keyword.lower().split())


def _catalog_id_for_keyword(keyword: str) -> int | None:
    """Sceglie il catalog_id Vinted in base alla keyword (euristica)."""
    foto_hints = {"canon", "nikon", "leica", "olympus", "obiettivo", "fotocamera", "tamron", "fujifilm", "lumix"}
    tech_hints = {"iphone", "thinkpad", "samsung", "macbook", "ipad", "console", "playstation", "nintendo"}
    kw_lower = keyword.lower()
    if any(h in kw_lower for h in foto_hints):
        return CATALOG_FOTOGRAFIA
    if any(h in kw_lower for h in tech_hints):
        return CATALOG_ELETTRONICA
    return None  # nessun filtro categoria → solo search_text


# ──────────────────────────────────────────────
# Fetch Vinted
# ──────────────────────────────────────────────

def _fetch_vinted(session: requests.Session, keyword: str, per_page: int = 96) -> list[dict]:
    """Fetch prima pagina di risultati dall'API Vinted per una keyword."""
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

    r = session.get(VINTED_API, params=params, timeout=20)
    if r.status_code != 200:
        raise ValueError(f"Vinted API returned {r.status_code} per keyword='{keyword}'")

    data = r.json()
    return data.get("items", [])


# ──────────────────────────────────────────────
# Scan & Save per keyword
# ──────────────────────────────────────────────

def _scan_vinted_keyword(session: requests.Session, keyword: str) -> dict:
    """
    Fetch Vinted + pre-filtro keyword/lingua + salvataggio annunci grezzi.
    scored=false per tutti i nuovi annunci — lo scorer esistente li prenderà
    al prossimo ciclo indipendentemente da source.
    """
    supabase = _get_supabase()

    # Fetch
    try:
        raw_items = _fetch_vinted(session, keyword)
    except Exception as e:
        return {"keyword": keyword, "error": str(e),
                "found": 0, "new": 0, "updated": 0, "skipped": 0, "rejected": 0}

    if not raw_items:
        return {"keyword": keyword, "found": 0, "new": 0,
                "updated": 0, "skipped": 0, "rejected": 0}

    # URL già in DB per questa keyword (source=Vinted.it)
    existing_response = (
        supabase.table("scan_results")
        .select("url, price_value")
        .eq("keyword", keyword)
        .eq("source", "Vinted.it")
        .execute()
    )
    existing = {row["url"]: row["price_value"] for row in (existing_response.data or [])}

    new_rows = []
    skipped  = 0
    rejected = 0
    updated  = 0

    for item in raw_items:
        url   = item.get("url", "")
        title = item.get("title", "")

        if not url:
            continue

        # Pre-filtro 1: keyword nel titolo
        if not _keyword_in_title(keyword, title):
            rejected += 1
            continue

        # Pre-filtro 2: lingua (scarta listing non italiani)
        if not _is_likely_italian(title):
            rejected += 1
            continue

        # Parsing prezzi
        price_raw, price_value = _parse_price(item.get("price"))
        _, total_price = _parse_price(item.get("total_item_price"))
        _, service_fee = _parse_price(item.get("service_fee"))

        if not price_value:
            continue

        # Price_raw esteso: include il costo reale per l'acquirente
        # Lo scorer leggerà source='Vinted.it' e userà total_price per il margine
        price_raw_full = f"{price_raw} (acquirente: {total_price:.2f} EUR, fee: {service_fee:.2f} EUR)" \
            if total_price and service_fee else price_raw

        # Location: Vinted espone city dentro l'oggetto user
        user_obj = item.get("user", {})
        location = user_obj.get("city", "") if isinstance(user_obj, dict) else ""

        condition = item.get("status", "non specificata") or "non specificata"

        # Gestione duplicati
        if url in existing:
            old_price = existing[url]
            if old_price == price_value:
                skipped += 1
                continue
            if old_price and (old_price - price_value) / old_price >= 0.15:
                # Calo >= 15%: reset scored per re-scoring e notifica
                supabase.table("scan_results").update({
                    "price_raw": price_raw_full,
                    "price_value": price_value,
                    "scored": False,
                    "score": None,
                    "margine_stimato": None,
                    "motivazione": None,
                    "rischi": None,
                }).eq("url", url).execute()
                # Reset notifications_log
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
                # Variazione < 15%: aggiorna solo prezzo
                supabase.table("scan_results").update({
                    "price_raw": price_raw_full,
                    "price_value": price_value,
                }).eq("url", url).execute()
                skipped += 1
            continue

        # Nuovo annuncio
        new_rows.append({
            "keyword":    keyword,
            "title":      title,
            "price_raw":  price_raw_full,
            "price_value": price_value,
            "location":   location,
            "url":        url,
            "date_listed": None,  # Vinted API non espone data listing
            "source":     "Vinted.it",
            "scored":     False,
            "condition":  condition,
        })

    if new_rows:
        supabase.table("scan_results").upsert(
            new_rows, on_conflict="url", ignore_duplicates=True
        ).execute()

    return {
        "keyword": keyword,
        "found":    len(raw_items),
        "new":      len(new_rows),
        "updated":  updated,
        "skipped":  skipped,
        "rejected": rejected,
    }


# ──────────────────────────────────────────────
# Entry point (async, pattern identico a scanner.py)
# ──────────────────────────────────────────────

async def run_vinted_scan() -> dict:
    """
    Cron job: fetch Vinted per ogni keyword attiva, salva annunci grezzi.
    Pattern identico a run_scan_and_save() in scanner.py.
    """
    try:
        # Session Vinted: una sola per tutto il ciclo (token valido ~2h)
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
            await asyncio.sleep(3)  # pausa gentile tra keyword
        result = await asyncio.to_thread(_scan_vinted_keyword, session, keyword)
        results.append(result)

    total_new = sum(r.get("new", 0) for r in results)

    return {
        "status": "ok",
        "source": "Vinted.it",
        "keywords_scanned": len(keywords),
        "total_new": total_new,
        "results": results,
    }
