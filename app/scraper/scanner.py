"""
scanner.py — Fetch Subito.it e salvataggio annunci grezzi.

Responsabilità:
- Fetch pagina Subito via _fetch_html (router per SCRAPER_MODE)
- Parsing __NEXT_DATA__ JSON
- Pre-filtro keyword (titolo/body) — zero costi AI
- Salvataggio annunci grezzi con scored=false
- Rilevamento cambio prezzo >= 15% → reset scored=false per re-scoring
- Nessuna chiamata a Claude né a eBay
"""

import os
import re
import json
import time
import asyncio
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

SCRAPERAPI_KEY    = os.getenv("SCRAPERAPI_KEY")
SCRAPERAPI_URL    = "http://api.scraperapi.com"

WEBSHARE_PROXY_USER = os.getenv("WEBSHARE_PROXY_USER")
WEBSHARE_PROXY_PASS = os.getenv("WEBSHARE_PROXY_PASS")
WEBSHARE_PROXY_HOST = "p.webshare.io"
WEBSHARE_PROXY_PORT = "80"

WEBSHARE_PROXIES = [
    ("31.59.20.176",    "6754"),
    ("198.23.239.134",  "6540"),
    ("31.56.127.193",   "7684"),
    ("45.38.107.97",    "6014"),
    ("107.172.163.27",  "6543"),
    ("216.10.27.159",   "6837"),
    ("142.111.67.146",  "5611"),
    ("191.96.254.138",  "6185"),
    ("31.58.9.4",       "6077"),
    ("23.229.19.94",    "8689"),
]
_proxy_index = 0

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

SCRAPER_MODE = os.getenv("SCRAPER_MODE", "scraperapi").lower()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ──────────────────────────────────────────────
# Metodi di fetch
# ──────────────────────────────────────────────

def _fetch_via_scraperapi(url: str) -> requests.Response:
    params = {"api_key": SCRAPERAPI_KEY, "url": url}
    for attempt in range(3):
        try:
            r = requests.get(SCRAPERAPI_URL, params=params, timeout=60)
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def _fetch_via_webshare(url: str) -> requests.Response:
    global _proxy_index
    errors = []
    for attempt in range(len(WEBSHARE_PROXIES)):
        host, port = WEBSHARE_PROXIES[_proxy_index % len(WEBSHARE_PROXIES)]
        _proxy_index += 1
        proxy_url = f"http://{WEBSHARE_PROXY_USER}:{WEBSHARE_PROXY_PASS}@{host}:{port}"
        proxies = {"http": proxy_url, "https": proxy_url}
        try:
            r = requests.get(url, headers=HEADERS, proxies=proxies, timeout=30)
            r.raise_for_status()
            return r
        except Exception as e:
            errors.append(f"{host}:{port} -> {str(e)[:50]}")
            time.sleep(2)
    raise Exception(f"Tutti i proxy falliti: {errors}")


def _fetch_direct(url: str) -> requests.Response:
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def _fetch_html(url: str) -> requests.Response:
    if SCRAPER_MODE == "webshare":
        return _fetch_via_webshare(url)
    elif SCRAPER_MODE == "direct":
        return _fetch_direct(url)
    else:
        return _fetch_via_scraperapi(url)


# ──────────────────────────────────────────────
# Estrazione dati
# ──────────────────────────────────────────────

def _extract_price_value(price_str: str) -> float | None:
    if not price_str or price_str == "N/D":
        return None
    cleaned = re.sub(r"[^\d,.]", "", price_str).replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_price(ad: dict) -> str:
    features = ad.get("features", {})
    if isinstance(features, dict):
        price_feature = features.get("/price", {})
        vals = price_feature.get("values", [])
        if vals and isinstance(vals[0], dict):
            return vals[0].get("value", vals[0].get("key", "N/D"))
    return "N/D"


def _extract_shipping(ad: dict) -> str:
    features = ad.get("features", {})
    if isinstance(features, dict):
        shipping_feature = features.get("/shipping")
        if shipping_feature:
            vals = shipping_feature.get("values", [])
            if vals and isinstance(vals[0], dict):
                return vals[0].get("value", "non specificata")
    return "non specificata"


def _extract_condition(ad: dict) -> str:
    features = ad.get("features", {})
    if isinstance(features, dict):
        condition_feature = features.get("/item_condition")
        if condition_feature:
            vals = condition_feature.get("values", [])
            if vals and isinstance(vals[0], dict):
                return vals[0].get("value", "non specificata")
    return "non specificata"


def _extract_url(ad: dict) -> str:
    urls = ad.get("urls", {})
    if isinstance(urls, dict) and urls:
        return urls.get("default", next(iter(urls.values()), ""))
    urn = ad.get("urn", "")
    if urn:
        parts = urn.split(":")
        if len(parts) >= 5:
            return f"https://www.subito.it/annunci/{parts[-1]}.htm"
    return ""


def _extract_location(ad: dict) -> str:
    geo = ad.get("geo", {})
    if not isinstance(geo, dict):
        return ""
    city = geo.get("city", {})
    region = geo.get("region", {})
    city_val = city.get("value", "") if isinstance(city, dict) else str(city)
    region_val = region.get("value", "") if isinstance(region, dict) else str(region)
    return f"{city_val}, {region_val}".strip(", ")


# ──────────────────────────────────────────────
# Fetch Subito
# ──────────────────────────────────────────────

def _fetch_subito(keyword: str, max_results: int = 30) -> list[dict]:
    search_url = (
        f"https://www.subito.it/annunci-italia/vendita/usato/"
        f"?q={keyword.replace(' ', '+')}&sort=date_desc"
    )
    response = _fetch_html(search_url)
    soup = BeautifulSoup(response.text, "html.parser")
    next_data_tag = soup.find("script", id="__NEXT_DATA__")
    if not next_data_tag:
        raise ValueError("__NEXT_DATA__ non trovato")

    next_data = json.loads(next_data_tag.string)
    items_data = (
        next_data
        .get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
        .get("items", {})
    )
    ads_raw = items_data.get("originalList", []) if isinstance(items_data, dict) else []

    results = []
    for ad in ads_raw[:max_results]:
        if not isinstance(ad, dict):
            continue
        price_raw = _extract_price(ad)
        results.append({
            "title": ad.get("subject", "N/D"),
            "price": price_raw,
            "price_value": _extract_price_value(price_raw),
            "location": _extract_location(ad),
            "date": ad.get("date", ""),
            "url": _extract_url(ad),
            "body": ad.get("body", ""),
            "shipping": _extract_shipping(ad),
            "condition": _extract_condition(ad),
            "source": "Subito.it",
        })
    return results


# ──────────────────────────────────────────────
# Scan & Save (solo dati grezzi, zero AI/eBay)
# ──────────────────────────────────────────────

def _get_active_keywords() -> list[str]:
    supabase = _get_supabase()
    response = (
        supabase.table("keywords")
        .select("keyword")
        .eq("active", True)
        .or_("only_collector.eq.false,only_collector.is.null")
        .execute()
    )
    return [row["keyword"].lower() for row in (response.data or [])]


def _scan_keyword(keyword: str) -> dict:
    """
    Fetch Subito + pre-filtro keyword + salvataggio annunci grezzi.
    scored=false per tutti i nuovi annunci — verranno scorati da scorer.py.
    """
    supabase = _get_supabase()
    items = _fetch_subito(keyword, max_results=30)

    if not items:
        return {"keyword": keyword, "found": 0, "new": 0,
                "updated": 0, "skipped": 0, "rejected": 0}

    existing_response = (
        supabase.table("scan_results")
        .select("url, price_value")
        .eq("keyword", keyword)
        .execute()
    )
    existing = {row["url"]: row["price_value"] for row in (existing_response.data or [])}

    new_rows = []
    skipped = 0
    rejected = 0
    updated = 0

    for item in items:
        if not item.get("price_value"):
            continue

        url = item["url"]
        price_value = item["price_value"]

        if url in existing:
            old_price = existing[url]
            if old_price == price_value:
                skipped += 1
                continue
            # Prezzo cambiato >= 15% → reset scored per re-scoring
            if old_price and (old_price - price_value) / old_price >= 0.15:
                supabase.table("scan_results").update({
                    "price_raw": item["price"],
                    "price_value": price_value,
                    "scored": False,
                    "score": None,
                    "margine_stimato": None,
                    "motivazione": None,
                    "rischi": None,
                    "ebay_valore_mercato": None,
                }).eq("url", url).execute()
                # Resetta notifications_log per rinotificare
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
                # Variazione < 15% — aggiorna solo il prezzo
                supabase.table("scan_results").update({
                    "price_raw": item["price"],
                    "price_value": price_value,
                }).eq("url", url).execute()
                skipped += 1
            continue

        # Pre-filtro: keyword deve apparire nel titolo o nei primi 200 char del body
        if (keyword not in item["title"].lower() and
                keyword not in item.get("body", "")[:200].lower()):
            rejected += 1
            continue

        new_rows.append({
            "keyword": keyword,
            "title": item["title"],
            "price_raw": item["price"],
            "price_value": price_value,
            "location": item["location"],
            "url": url,
            "date_listed": item["date"] or None,
            "source": item["source"],
            "body": item.get("body", ""),
            "shipping": item.get("shipping", "non specificata"),
            "scored": False,
            "condition": item.get("condition", "non specificata"),
        })

    if new_rows:
        supabase.table("scan_results").upsert(
            new_rows, on_conflict="url", ignore_duplicates=True
        ).execute()

    return {
        "keyword": keyword,
        "found": len(items),
        "new": len(new_rows),
        "updated": updated,
        "skipped": skipped,
        "rejected": rejected,
        "scraper_mode": SCRAPER_MODE,
    }


async def run_lepe_scan(keyword: str, max_results: int = 15) -> list[dict]:
    """Endpoint /test-scan: ritorna annunci senza filtrare né salvare."""
    try:
        return await asyncio.to_thread(_fetch_subito, keyword, max_results)
    except requests.exceptions.HTTPError as e:
        return [{"title": "Errore HTTP", "price": str(e), "source": "Subito.it"}]
    except requests.exceptions.Timeout:
        return [{"title": "Timeout", "price": "Nessuna risposta in tempo", "source": "Subito.it"}]
    except ValueError as e:
        return [{"title": "Parsing fallito", "price": str(e), "source": "Subito.it"}]
    except Exception as e:
        return [{"title": "Errore Tecnico", "price": str(e), "source": "Subito.it"}]


async def run_scan_and_save() -> dict:
    """
    Cron job: fetch Subito per ogni keyword attiva, salva annunci grezzi.
    Nessuna chiamata AI o eBay — veloce e resiliente.
    """
    try:
        keywords = await asyncio.to_thread(_get_active_keywords)
        if not keywords:
            return {"status": "ok", "message": "Nessuna keyword attiva"}

        results = []
        for i, keyword in enumerate(keywords):
            if i > 0:
                await asyncio.sleep(10)
            result = await asyncio.to_thread(_scan_keyword, keyword)
            results.append(result)

        return {
            "status": "ok",
            "scraper_mode": SCRAPER_MODE,
            "keywords_scanned": len(keywords),
            "results": results,
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}
