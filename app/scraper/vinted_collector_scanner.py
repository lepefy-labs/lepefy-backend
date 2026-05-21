"""
vinted_collector_scanner.py — Fetch annunci Vinted per collezionisti.

Stesso pipeline di vinted_defective_scanner.py ma con:
    - Nessun filtro condizione (status_id) — tutti gli annunci
    - keyword lette da keywords WHERE only_collector=true
    - Filtro prezzo dalla fascia min/max delle subscription con is_collector=true
    - scored=True all'inserimento (bypass scorer — collezionisti cercano oggetti
      specifici, non margine di rivendita)
    - TITLE_BLACKLIST: scarta annunci con parole non pertinenti nel titolo
      (accessori moda, oggettistica, libri, ecc.)

Prerequisiti DB:
    ALTER TABLE keywords
    ADD COLUMN IF NOT EXISTS only_collector boolean DEFAULT false;

    ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS is_collector boolean DEFAULT false;

    ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS source text DEFAULT NULL;
"""

import os
import json
import time
import asyncio
import requests
from collections import defaultdict
from supabase import create_client, Client

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

VINTED_HOME       = "https://www.vinted.it"
VINTED_SEARCH_API = f"{VINTED_HOME}/api/v2/catalog/items"
VINTED_USER_API   = f"{VINTED_HOME}/api/v2/users"

ALLOWED_COUNTRIES = {"IT", "FR"}

# Parole nel titolo che indicano annunci non pertinenti (moda, oggettistica, ecc.)
# Applicate case-insensitive prima del salvataggio in scan_results.
TITLE_BLACKLIST = {
    "zaino", "borsa", "orologio", "cinturino", "occhiali", "profumo",
    "scarpe", "cappello", "giacca", "pantaloni", "gonna", "vestito",
    "felpa", "maglione", "camicia", "stampa", "poster", "libro",
    "manuale", "custodia", "cover", "skin",
}

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
HEADERS_HTML = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
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
    if isinstance(price_obj, dict):
        try:
            amount   = float(price_obj.get("amount", 0))
            currency = price_obj.get("currency_code", "EUR")
            return f"{amount:.2f} {currency}", amount
        except (ValueError, TypeError):
            pass
    return "N/D", None


# ──────────────────────────────────────────────
# User API — paese e città
# ──────────────────────────────────────────────

def _fetch_user_details(session: requests.Session, user_ids: list[int]) -> dict[int, dict]:
    result = {}
    for uid in user_ids:
        try:
            r = session.get(f"{VINTED_USER_API}/{uid}", timeout=15)
            if r.status_code != 200:
                result[uid] = {"country_code": None, "location": ""}
                continue
            user          = r.json().get("user", {})
            country_code  = user.get("country_code") or user.get("country_iso_code")
            country_title = user.get("country_title", "")
            city          = user.get("city", "") if user.get("expose_location") else ""
            if city and country_title:
                location = f"{city}, {country_title}"
            elif country_title:
                location = country_title
            else:
                location = ""
            result[uid] = {"country_code": country_code, "location": location}
        except Exception:
            result[uid] = {"country_code": None, "location": ""}
        time.sleep(0.3)
    return result


# ──────────────────────────────────────────────
# HTML fetch — descrizione
# ──────────────────────────────────────────────

def _fetch_item_descriptions(session: requests.Session, items: list[dict]) -> dict[int, str]:
    KEY     = '"description":'
    decoder = json.JSONDecoder()
    result  = {}
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
            html      = r.text
            idx       = html.find(KEY)
            if idx == -1:
                result[iid] = ""
                continue
            val_start = idx + len(KEY)
            while val_start < len(html) and html[val_start] in " \t\n\r":
                val_start += 1
            if val_start >= len(html) or html[val_start] != '"':
                result[iid] = ""
                continue
            value, _ = decoder.raw_decode(html, val_start)
            result[iid] = value if isinstance(value, str) else ""
        except Exception:
            result[iid] = ""
        time.sleep(0.5)
    return result


# ──────────────────────────────────────────────
# Filtri
# ──────────────────────────────────────────────

def _keyword_in_title(keyword: str, title: str) -> bool:
    if not title:
        return False
    title_lower = title.lower()
    return all(token in title_lower for token in keyword.lower().split())


def _is_blacklisted(title: str) -> bool:
    """True se il titolo contiene almeno una parola della TITLE_BLACKLIST."""
    title_lower = title.lower()
    return any(word in title_lower for word in TITLE_BLACKLIST)


def _price_in_any_range(price: float, ranges: list[tuple[float, float]]) -> bool:
    return any(lo <= price <= hi for lo, hi in ranges)


# ──────────────────────────────────────────────
# Fetch Vinted — tutti gli status (no filtro condizione)
# ──────────────────────────────────────────────

def _fetch_vinted_collector(
    session: requests.Session,
    keyword: str,
    per_page: int = 96,
) -> list[dict]:
    params = {
        "page":        1,
        "per_page":    per_page,
        "search_text": keyword,
        "order":       "newest_first",
        "time":        int(time.time()),
    }
    r = session.get(VINTED_SEARCH_API, params=params, timeout=20)
    if r.status_code != 200:
        raise ValueError(f"Vinted API returned {r.status_code}")
    return r.json().get("items", [])


# ──────────────────────────────────────────────
# Scan per keyword
# ──────────────────────────────────────────────

def _scan_collector_keyword(
    session: requests.Session,
    keyword: str,
    price_ranges: list[tuple[float, float]],
) -> dict:
    """
    Fetch annunci Vinted per una keyword collezionista.
    Filtra per fascia prezzo dalle subscription con is_collector=true.
    Salva con scored=True: nessun scoring AI per i collezionisti.
    """
    supabase = _get_supabase()

    try:
        raw_items = _fetch_vinted_collector(session, keyword)
    except Exception as e:
        return {
            "keyword": keyword, "found": 0, "new": 0,
            "updated": 0, "skipped": 0, "rejected": 0,
            "blacklisted": 0, "incomplete": 0, "errors": [str(e)],
        }

    if not raw_items:
        return {
            "keyword": keyword, "found": 0, "new": 0,
            "updated": 0, "skipped": 0, "rejected": 0,
            "blacklisted": 0, "incomplete": 0, "errors": [],
        }

    existing_response = (
        supabase.table("scan_results")
        .select("url, price_value")
        .eq("keyword", keyword)
        .eq("source", "Vinted.it")
        .execute()
    )
    existing = {row["url"]: row["price_value"] for row in (existing_response.data or [])}

    candidates = []
    rejected    = 0
    blacklisted = 0
    for item in raw_items:
        if not item.get("url"):
            continue
        title = item.get("title", "")
        if not _keyword_in_title(keyword, title):
            rejected += 1
            continue
        if _is_blacklisted(title):
            blacklisted += 1
            continue
        _, pv = _parse_price(item.get("price"))
        if not pv:
            continue
        if not _price_in_any_range(pv, price_ranges):
            rejected += 1
            continue
        candidates.append(item)

    truly_new = [c for c in candidates if c.get("url") not in existing]
    in_db     = [c for c in candidates if c.get("url") in existing]

    user_ids = list({
        item["user"]["id"]
        for item in truly_new
        if isinstance(item.get("user"), dict) and item["user"].get("id")
    })
    user_details = _fetch_user_details(session, user_ids) if user_ids else {}

    new_items_list    = [c for c in truly_new if c.get("id") and c.get("url")]
    item_descriptions = _fetch_item_descriptions(session, new_items_list) if new_items_list else {}

    new_rows   = []
    incomplete = 0
    for item in truly_new:
        url   = item.get("url", "")
        title = item.get("title", "")

        price_raw, price_value = _parse_price(item.get("price"))
        _, total_price         = _parse_price(item.get("total_item_price"))
        _, service_fee         = _parse_price(item.get("service_fee"))

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

        if not country or country not in ALLOWED_COUNTRIES:
            incomplete += 1
            continue

        new_rows.append({
            "keyword":     keyword,
            "title":       title,
            "price_raw":   price_raw_full,
            "price_value": price_value,
            "location":    location,
            "country":     country,
            "url":         url,
            "date_listed": None,
            "source":      "Vinted.it",
            "scored":      True,
            "condition":   condition,
            "image_url":   item.get("photo", {}).get("url") or None,
            "body":        body,
        })

    if new_rows:
        supabase.table("scan_results").upsert(
            new_rows, on_conflict="url", ignore_duplicates=True
        ).execute()

    skipped = 0
    updated = 0
    for item in in_db:
        url = item.get("url", "")

        price_raw, price_value = _parse_price(item.get("price"))
        _, total_price         = _parse_price(item.get("total_item_price"))
        _, service_fee         = _parse_price(item.get("service_fee"))

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
                "price_raw":       price_raw_full,
                "price_value":     price_value,
                "scored":          True,
                "score":           None,
                "margine_stimato": None,
                "motivazione":     None,
                "rischi":          None,
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
                "price_raw":   price_raw_full,
                "price_value": price_value,
            }).eq("url", url).execute()
            skipped += 1

    return {
        "keyword":        keyword,
        "found":          len(raw_items),
        "new":            len(new_rows),
        "updated":        updated,
        "skipped":        skipped,
        "rejected":       rejected,
        "blacklisted":    blacklisted,
        "incomplete":     incomplete,
        "user_api_calls": len(user_ids),
        "item_api_calls": len(new_items_list),
        "errors":         [],
    }


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

async def run_vinted_collector_scan() -> dict:
    """
    Cron job: fetch annunci Vinted per keyword con only_collector=True.
    Filtra per fascia prezzo dalle subscription con is_collector=True.
    """
    try:
        session = await asyncio.to_thread(_get_vinted_session)
    except Exception as e:
        return {"status": "error", "detail": f"Vinted session failed: {e}"}

    try:
        supabase    = _get_supabase()
        kw_response = (
            supabase.table("keywords")
            .select("keyword")
            .eq("active", True)
            .eq("only_collector", True)
            .execute()
        )
        keywords = [row["keyword"].lower() for row in (kw_response.data or [])]
    except Exception as e:
        return {"status": "error", "detail": f"Keywords fetch failed: {e}"}

    if not keywords:
        return {"status": "ok", "message": "Nessuna keyword con only_collector=true"}

    try:
        subs_response = (
            supabase.table("subscriptions")
            .select("keyword, min_threshold, max_threshold")
            .eq("active", True)
            .eq("is_collector", True)
            .execute()
        )
        price_ranges_by_kw: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for sub in (subs_response.data or []):
            kw  = sub["keyword"].lower()
            lo  = float(sub.get("min_threshold") or 0)
            hi  = float(sub.get("max_threshold") or 999999)
            price_ranges_by_kw[kw].append((lo, hi))
    except Exception as e:
        return {"status": "error", "detail": f"Subscriptions fetch failed: {e}"}

    results = []
    for i, keyword in enumerate(keywords):
        if i > 0:
            await asyncio.sleep(3)
        ranges = price_ranges_by_kw.get(keyword, [])
        if not ranges:
            results.append({"keyword": keyword, "skipped": "no active collector subscriptions"})
            continue
        result = await asyncio.to_thread(_scan_collector_keyword, session, keyword, ranges)
        results.append(result)

    return {
        "status":               "ok",
        "source":               "Vinted.it (collector)",
        "keywords_scanned":     len(keywords),
        "total_new":            sum(r.get("new", 0) for r in results),
        "total_blacklisted":    sum(r.get("blacklisted", 0) for r in results),
        "total_user_api_calls": sum(r.get("user_api_calls", 0) for r in results),
        "total_item_api_calls": sum(r.get("item_api_calls", 0) for r in results),
        "results":              results,
    }
