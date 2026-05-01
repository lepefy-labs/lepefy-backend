import os
import requests

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
# URL base senza parametri aggiuntivi
SUBITO_API = "https://www.subito.it/hades/v1/search/items/"

def run_lepe_scan(keyword: str, max_results: int = 15):
    try:
        if not SCRAPERAPI_KEY:
            return [{"title": "Errore", "price": "Chiave ScraperAPI mancante"}]

        # Costruiamo l'URL di destinazione con i parametri di Subito già inclusi
        # ma lo passiamo come stringa semplice a ScraperAPI
        target_url = f"{SUBITO_API}?q={keyword}&lim={max_results}&sort=datedesc&t=s"

        # Parametri per ScraperAPI
        payload = {
            "api_key": SCRAPERAPI_KEY,
            "url": target_url,
            "country_code": "it",
            "render": "false"
        }
        
        # Chiamata diretta
        response = requests.get("http://api.scraperapi.com", params=payload, timeout=30)
        
        # Se ScraperAPI dà ancora 404, stampiamo l'URL per debuggare nei log di Railway
        if response.status_code != 200:
            print(f"DEBUG - ScraperAPI URL inviato: {response.url}")
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
        return [{"title": "Errore", "price": str(e), "source": "Scanner"}]
