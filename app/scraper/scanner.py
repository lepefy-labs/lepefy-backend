import os
import requests

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

# Subito.it espone un'API JSON interna usata dal suo frontend.
# È molto più stabile dello scraping HTML (niente class name hashate, niente JS render).
SUBITO_API = "https://www.subito.it/hades/v1/search/items/"

def run_lepe_scan(keyword: str, max_results: int = 15):
    # 1. Definiamo i parametri originali per Subito
    subito_params = {
        "q": keyword,
        "lim": max_results,
        "start": 0,
        "sort": "datedesc",
        "t": "s",
    }

    try:
        if SCRAPERAPI_KEY:
            # Creiamo l'URL di Subito pulito con i suoi parametri
            # Usiamo un sistema più robusto per generare l'URL target
            target_req = requests.Request('GET', SUBITO_API, params=subito_params).prepare()
            target_url = target_req.url

            # Parametri per ScraperAPI
            payload = {
                "api_key": SCRAPERAPI_KEY,
                "url": target_url,
                "country_code": "it"
            }
            
            # Chiamata a ScraperAPI: i parametri vanno passati nel dizionario 'params'
            response = requests.get("http://api.scraperapi.com", params=payload, timeout=30)
        else:
            # Fallback locale senza proxy
            headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            response = requests.get(SUBITO_API, params=subito_params, headers=headers, timeout=15)

        response.raise_for_status()
        data = response.json()
        results = []

        for ad in data.get("ads", []):
            price_str = "N/D"
            for feature in ad.get("features", []):
                if feature.get("uri") == "/price":
                    values = feature.get("values", [])
                    price_str = values[0].get("value", "N/D") if values else "N/D"
                    break

            results.append({
                "title": ad.get("subject", "N/D"),
                "price": price_str,
                "location": ad.get("geo", {}).get("city", {}).get("value", "N/D"),
                "url": ad.get("urls", {}).get("default", ""),
                "source": "Subito API",
            })
        return results

    except Exception as e:
        return [{"title": "Errore", "price": str(e), "source": "Subito.it"}]
