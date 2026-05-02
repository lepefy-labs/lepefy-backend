import os
import requests

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

# Subito.it espone un'API JSON interna usata dal suo frontend.
# È molto più stabile dello scraping HTML (niente class name hashate, niente JS render).
SUBITO_API = "https://www.subito.it/hades/v1/search/items/"

def run_lepe_scan(keyword: str, max_results: int = 15) -> list[dict]:
    """
    Cerca annunci su Subito.it tramite la sua API JSON interna.
    Restituisce una lista di dict con title, price, location, url, date.
    """
    params = {
        "q": keyword,
        "lim": max_results,
        "start": 0,
        "sort": "datedesc",  # più recenti prima
        "t": "s",            # solo vendita
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.subito.it/",
    }

    try:
        if SCRAPERAPI_KEY:
            # Passa la richiesta tramite ScraperAPI per evitare blocchi IP
            proxy_params = {
                "api_key": SCRAPERAPI_KEY,
                "url": requests.Request("GET", SUBITO_API, params=params).prepare().url,
                "country_code": "it",
                # render=false: è JSON puro, non serve JS rendering → più veloce e meno costoso
            }
            response = requests.get("http://api.scraperapi.com", params=proxy_params, timeout=30)
        else:
            # Senza ScraperAPI: funziona per test locali, può essere bloccato in prod
            response = requests.get(SUBITO_API, params=params, headers=headers, timeout=15)

        response.raise_for_status()
        data = response.json()

        results = []

        # La risposta JSON ha struttura: {"ads": [...]}
        ads = data.get("ads", [])

        for ad in ads:
            # Estrai prezzo
            prices = ad.get("features", [])
            price_str = "N/D"
            for feature in prices:
                if feature.get("uri") == "/price":
                    values = feature.get("values", [])
                    if values:
                        price_str = values[0].get("value", "N/D")
                    break

            # Estrai posizione
            geo = ad.get("geo", {})
            city = geo.get("city", {}).get("value", "")
            region = geo.get("region", {}).get("value", "")
            location = f"{city}, {region}".strip(", ")

            # Estrai URL
            urls = ad.get("urls", {})
            ad_url = urls.get("default", "")

            results.append({
                "title": ad.get("subject", "N/D"),
                "price": price_str,
                "location": location,
                "date": ad.get("date", ""),
                "url": ad_url,
                "source": "Subito.it",
            })

        return results

    except requests.exceptions.HTTPError as e:
        return [{"title": "Errore HTTP", "price": str(e), "source": "Subito.it"}]
    except requests.exceptions.Timeout:
        return [{"title": "Timeout", "price": "Subito non ha risposto in tempo", "source": "Subito.it"}]
    except Exception as e:
        return [{"title": "Errore Tecnico", "price": str(e), "source": "Subito.it"}]


# --- Test rapido ---
if __name__ == "__main__":
    keyword = "ThinkPad"
    print(f"Cerco: {keyword}\n")
    results = run_lepe_scan(keyword, max_results=5)
    for r in results:
        print(f"[{r['price']}] {r['title']}")
        print(f"  📍 {r['location']}  |  {r['date']}")
        print(f"  🔗 {r['url']}\n")
