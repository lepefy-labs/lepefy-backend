import os
import requests
from bs4 import BeautifulSoup
import urllib.parse

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

def run_lepe_scan(keyword: str, max_results: int = 15):
    try:
        if not SCRAPERAPI_KEY:
            return [{"title": "Errore", "price": "Chiave ScraperAPI mancante"}]

        # URL di ricerca Etsy
        target_url = f"https://www.etsy.com/it/search?q={urllib.parse.quote(keyword)}"
        
        # Per Etsy NON serve premium=true né render=true, risparmiamo crediti!
        payload = {
            "api_key": SCRAPERAPI_KEY,
            "url": target_url,
            "country_code": "it"
        }

        print(f"DEBUG - Scansione Etsy per: {keyword}")
        response = requests.get("http://api.scraperapi.com", params=payload, timeout=30)
        
        if response.status_code != 200:
            return [{"title": f"Errore {response.status_code}", "price": "Riprova tra poco", "source": "Etsy"}]

        soup = BeautifulSoup(response.text, 'html.parser')
        results = []

        # Selettore per i prodotti Etsy (molto stabile)
        items = soup.select(".v2-listing-card") or soup.select(".listing-link")

        for item in items[:max_results]:
            title_el = item.select_one("h3") or item.select_one(".v2-listing-card__title")
            price_el = item.select_one(".currency-value")
            link_el = item.get("href") or (item.select_one("a")['href'] if item.select_one("a") else None)

            if title_el and price_el:
                results.append({
                    "title": title_el.get_text(strip=True),
                    "price": "€ " + price_el.get_text(strip=True),
                    "url": link_el if link_el.startswith("http") else f"https://www.etsy.com{link_el}",
                    "source": "Etsy"
                })

        return results

    except Exception as e:
        return [{"title": "Errore Tecnico", "price": str(e), "source": "Etsy"}]
