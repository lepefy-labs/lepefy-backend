import os
import json
import asyncio
import requests
from bs4 import BeautifulSoup

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
SCRAPERAPI_URL = "http://api.scraperapi.com"


def _fetch_subito(keyword: str, max_results: int = 15) -> list[dict]:
    """
    Scarica la pagina di ricerca Subito.it (con JS rendering via ScraperAPI)
    ed estrae gli annunci dal blocco __NEXT_DATA__ che Next.js inietta nel DOM.
    Più stabile dei selettori CSS o delle API interne.
    """
    search_url = (
        f"https://www.subito.it/annunci-italia/vendita/usato/"
        f"?q={keyword.replace(' ', '+')}&sort=date_desc"
    )

    if SCRAPERAPI_KEY:
        params = {
            "api_key": SCRAPERAPI_KEY,
            "url": search_url,
            "render": "true",       # serve il JS per popolare __NEXT_DATA__
            "country_code": "it",
        }
        response = requests.get(SCRAPERAPI_URL, params=params, timeout=60)
    else:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(search_url, headers=headers, timeout=20)

    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    # Next.js inietta tutti i dati della pagina in questo tag script
    next_data_tag = soup.find("script", id="__NEXT_DATA__")
    if not next_data_tag:
        raise ValueError("__NEXT_DATA__ non trovato — pagina non renderizzata correttamente")

    next_data = json.loads(next_data_tag.string)

    # Path principale nella struttura JSON di Subito
    ads_raw = (
        next_data
        .get("props", {})
        .get("pageProps", {})
        .get("dehydratedState", {})
        .get("queries", [{}])[0]
        .get("state", {})
        .get("data", {})
        .get("ads", [])
    )

    # Path alternativo
    if not ads_raw:
        ads_raw = (
            next_data
            .get("props", {})
            .get("pageProps", {})
            .get("initialData", {})
            .get("ads", [])
        )

    results = []
    for ad in ads_raw[:max_results]:
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

    return results


async def run_lepe_scan(keyword: str, max_results: int = 15) -> list[dict]:
    """
    Wrapper asincrono — esegue la chiamata bloccante in un thread separato
    senza bloccare l'event loop di FastAPI.
    """
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


if __name__ == "__main__":
    async def main():
        results = await run_lepe_scan("ThinkPad", max_results=5)
        for r in results:
            print(f"[{r['price']}] {r['title']}")
            print(f"  📍 {r['location']}  |  {r['date']}")
            print(f"  🔗 {r['url']}\n")

    asyncio.run(main())
