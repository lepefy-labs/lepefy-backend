import os
import re
import json
import asyncio
import requests
import httpx
from bs4 import BeautifulSoup
from supabase import create_client, Client

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
SCRAPERAPI_URL = "http://api.scraperapi.com"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _extract_price_value(price_str: str) -> float | None:
    """Estrae il valore numerico da stringhe tipo '275 €' o '1.200 €'."""
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


def _score_ad(title: str, price: str, location: str, body: str) -> dict:
    """
    Chiama Claude Haiku per valutare l'annuncio.
    Ritorna score, verdict, valore_stimato, margine_stimato, motivazione, rischi.
    """
    prompt = f"""Sei un esperto di elettronica usata e flipping su marketplace italiani (Subito.it).

Annuncio:
Titolo: {title}
Prezzo: {price}
Città: {location}
Descrizione: {body[:800] if body else 'N/D'}

Valuta se è un buon affare per il flipping. Rispondi SOLO con JSON valido, nessun testo extra:
{{"score":7,"verdict":"AFFARE","valore_stimato":320,"margine_stimato":70,"motivazione":"Descrizione sintetica della valutazione in max 15 parole","rischi":"Principali rischi in max 10 parole oppure stringa vuota se nessuno"}}

Valori possibili per verdict: AFFARE, OK, EVITA
Score da 1 a 10 dove 10 è l'affare perfetto."""

    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        text = data["content"][0]["text"].replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        return {
            "score": None,
            "verdict": "N/D",
            "valore_stimato": None,
            "margine_stimato": None,
            "motivazione": f"Scoring non disponibile: {str(e)[:50]}",
            "rischi": "",
        }


def _fetch_subito(keyword: str, max_results: int = 30) -> list[dict]:
    search_url = (
        f"https://www.subito.it/annunci-italia/vendita/usato/"
        f"?q={keyword.replace(' ', '+')}&sort=date_desc"
    )
    params = {
        "api_key": SCRAPERAPI_KEY,
        "url": search_url,
        "render": "true",
        "country_code": "it",
    }
    response = requests.get(SCRAPERAPI_URL, params=params, timeout=60)
    response.raise_for_status()

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
            "source": "Subito.it",
        })
    return results


def _save_deals(keyword: str, items: list[dict], price_threshold: float) -> int:
    """
    Filtra annunci sotto soglia, li valuta con AI e salva su Supabase.
    Ritorna il numero di record inseriti.
    """
    supabase = _get_supabase()
    deals = [
        i for i in items
        if i.get("price_value") and i["price_value"] <= price_threshold
    ]

    if not deals:
        return 0

    rows = []
    for d in deals:
        ai = _score_ad(d["title"], d["price"], d["location"], d.get("body", ""))
        rows.append({
            "keyword": keyword,
            "title": d["title"],
            "price_raw": d["price"],
            "price_value": d["price_value"],
            "location": d["location"],
            "url": d["url"],
            "date_listed": d["date"] or None,
            "source": d["source"],
            "notified": False,
            "score": ai.get("score"),
            "margine_stimato": ai.get("margine_stimato"),
            "motivazione": ai.get("motivazione"),
            "rischi": ai.get("rischi"),
        })

    supabase.table("scan_results").upsert(
        rows, on_conflict="url", ignore_duplicates=True
    ).execute()

    return len(rows)


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


async def run_scan_and_save(keyword: str, price_threshold: float) -> dict:
    """Cron job: scansiona, valuta con AI, filtra sotto soglia, salva su Supabase."""
    try:
        items = await asyncio.to_thread(_fetch_subito, keyword, 30)
        saved = await asyncio.to_thread(_save_deals, keyword, items, price_threshold)
        return {
            "status": "ok",
            "keyword": keyword,
            "threshold": price_threshold,
            "found": len(items),
            "saved": saved,
        }
    except Exception as e:
        return {"status": "error", "keyword": keyword, "detail": str(e)}
