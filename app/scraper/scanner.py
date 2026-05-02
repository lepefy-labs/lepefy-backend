import os
import json
import asyncio
import requests
from bs4 import BeautifulSoup

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
SCRAPERAPI_URL = "http://api.scraperapi.com"


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

    # Path confermato dalla risposta debug:
    # props.pageProps.initialState.items
    items_data = (
        next_data
        .get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
        .get("items", {})
    )

    # Path confermato: items.originalList contiene gli annunci
    ads_raw = items_data.get("originalList", []) if isinstance(items_data, dict) else []

    results = []
    for ad in ads_raw[:max_results]:
        try:
            if not isinstance(ad, dict):
                results.append({"title": "DEBUG: tipo non dict", "price": str(type(ad)), "raw": str(ad)[:300], "source": "Subito.it"})
                break
        price_str = "N/D"
        for feature in ad.get("features", []):
            if feature.get("uri") == "/price":
                vals = feature.get("values", [])
                if vals:
                    price_str = vals[0].get("value", "N/D")
                break

            # Estrai prezzo dalle features
            price_str = "N/D"
            for feature in ad.get("features", []):
                if feature.get("uri") == "/price":
                    vals = feature.get("values", [])
                    if vals:
                        price_str = vals[0].get("value", "N/D")
                    break

            geo = ad.get("geo", {})
            city = geo.get("city", {}).get("value", "")
            region = geo.get("region", {}).get("value", "")
            location = f"{city}, {region}".strip(", ")

            results.append({
                "title": ad.get("subject", "N/D"),
                "price": price_str,
                "location": location,
                "date": ad.get("date", ""),
                "url": ad.get("urls", {}).get("default", ""),
                "source": "Subito.it",
            })
        except Exception as e:
            results.append({"title": "Errore parsing ad", "price": str(e), "raw": str(ad)[:200], "source": "Subito.it"})
            break

    if not results:
        first = ads_raw[0] if ads_raw else "lista vuota"
        return [{"title": "DEBUG: primo elemento", "price": str(type(first)), "raw": str(first)[:500], "source": "Subito.it"}]

    return results


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
