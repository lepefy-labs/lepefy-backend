import os
import requests
import urllib.parse

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
SUBITO_API = "https://www.subito.it/hades/v1/search/items/"

def run_lepe_scan(keyword: str, max_results: int = 15):
    try:
        if not SCRAPERAPI_KEY:
            return [{"title": "Errore", "price": "Chiave ScraperAPI mancante"}]

        # 1. Costruiamo l'URL target
        target_url = f"{SUBITO_API}?q={keyword}&lim={max_results}&sort=datedesc&t=s"
        encoded_url = urllib.parse.quote(target_url)

        # 2. Prepariamo i parametri per ScraperAPI
        # keep_headers=true dice a ScraperAPI di usare i nostri headers
        final_api_url = f"http://api.scraperapi.com/?api_key={SCRAPERAPI_KEY}&url={encoded_url}&country_code=it&keep_headers=true"
        
        # 3. Headers che "simulano" una richiesta legittima dall'App/Sito di Subito
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://www.subito.it/"
        }

        print(f"DEBUG - Tentativo finale con keep_headers su: {target_url}")

        response = requests.get(final_api_url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            # Se dà ancora 404, proviamo a capire se è ScraperAPI o Subito
            print(f"DEBUG - Fallimento ({response.status_code}): {response.text[:200]}")
            return [{"title": f"Errore {response.status_code}", "price": "Controlla Logs", "source": "ScraperAPI"}]

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
        return [{"title": "Errore", "price": str(e), "source": "Scanner"}]
