import os
import json
import asyncio
import requests
from bs4 import BeautifulSoup

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
SCRAPERAPI_URL = "http://api.scraperapi.com"

# Imposta True per vedere la struttura grezza del primo annuncio
DEBUG_FIRST_AD = os.getenv("DEBUG_FIRST_AD", "false").lower() == "true"


def _extract_price(ad: dict) -> str:
    """Prova diverse strutture possibili per il prezzo."""
    # Struttura 1: features lista di dict con uri/values
    for feature in ad.get("features", []):
        if isinstance(feature, dict) and feature.get("uri") == "/price":
            vals = feature.get("values", [])
            if vals and isinstance(vals[0], dict):
                return vals[0].get("value", "N/D")
            if vals and isinstance(vals[0], str):
                return vals[0]

    # Struttura 2: price diretta
    if "price" in ad:
        p = ad["price"]
        if isinstance(p, dict):
            return str(p.get("value", p.get("amount", "N/D")))
        return str(p)

    # Struttura 3: advertiser/pricing
    pricing = ad.get("pricing", {})
    if pricing:
        return str(pricing.get("value", pricing.get("price", "N/D")))

    return "N/D"


def _extract_title(ad: dict) -> str:
    return ad.get("subject") or ad.get("title") or ad.get("name") or "N/D"


def _extract_url(ad: dict) -> str:
    urls = ad.get("urls", {})
    if isinstance(urls, dict):
        return urls.get("default", urls.get("web", ""))
    urn = ad.get("urn", "")
    # Prova a costruire URL da urn: "id:ad:UUID:list:ID" -> /annunci/ID
    if urn:
        parts = urn.split(":")
        if len(parts) >= 5:
            return f"https://www.subito.it/annunci/{parts[-1]}.htm"
    return ""


def _extract_location(ad: dict) -> str:
    geo = ad.get("geo", {})
    if isinstance(geo, dict):
        city = geo.get("city", {}).get("value", "") if isinstance(geo.get("city"), dict) else geo.get("city", "")
        region = geo.get("region", {}).get("value", "") if isinstance(geo.get("region"), dict) else geo.get("region", "")
        return f"{city}, {region}".strip(", ")
    return ""


def _fetch_subito(keyword: str, max_results: int = 15) -> list[dict]:
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

    if not ads_raw:
        return [{"title": "DEBUG: originalList vuota", "price": "N/D", "source": "Subito.it"}]

    # Debug: dump completo del primo annuncio per capire la struttura
    if DEBUG_FIRST_AD:
        return [{"title": "DEBUG FULL AD", "raw": json.dumps(ads_raw[0], ensure_ascii=False)[:2000], "source": "Subito.it"}]

    results = []
    for ad in ads_raw[:max_results]:
        if not isinstance(ad, dict):
            continue
        results.append({
            "title": _extract_title(ad),
            "price": _extract_price(ad),
            "location": _extract_location(ad),
            "date": ad.get("date", ""),
            "url": _extract_url(ad),
            "source": "Subito.it",
        })

    return results if results else [{"title": "Nessun annuncio estratto", "price": "N/D", "source": "Subito.it"}]


async def run_lepe_scan(keyword: str, max_results: int = 15) -> list[dict]:
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
