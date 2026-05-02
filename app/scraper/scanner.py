import os
import json
import asyncio
import requests
from bs4 import BeautifulSoup

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
SCRAPERAPI_URL = "http://api.scraperapi.com"


def _extract_price(ad: dict) -> str:
    # features è un dict: {"/price": {"label": "Prezzo", "values": [{"key": "450", "value": "450 €"}]}}
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
    # Fallback da urn: "id:ad:UUID:list:ID"
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
        return [{"title": "Nessun risultato", "price": "N/D", "source": "Subito.it"}]

    results = []
    for ad in ads_raw[:max_results]:
        if not isinstance(ad, dict):
            continue
        results.append({
            "title": ad.get("subject", "N/D"),
            "price": _extract_price(ad),
            "location": _extract_location(ad),
            "date": ad.get("date", ""),
            "url": _extract_url(ad),
            "source": "Subito.it",
        })

    return results or [{"title": "Nessun annuncio estratto", "price": "N/D", "source": "Subito.it"}]


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
