import os
import requests
from bs4 import BeautifulSoup
import urllib.parse
import time

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

def run_lepe_scan(keyword: str, max_results: int = 15):
    if not SCRAPERAPI_KEY:
        return [{"title": "Errore", "price": "Chiave ScraperAPI mancante"}]

    target_url = f"https://www.etsy.com/it/search?q={urllib.parse.quote(keyword)}"
    
    payload = {
        "api_key": SCRAPERAPI_KEY,
        "url": target_url,
        "country_code": "it",
        "render": "false" # Restiamo su false per velocità
    }

    # Tentiamo la chiamata fino a 2 volte in caso di timeout
    for attempt in range(2):
        try:
            print(f"DEBUG - Tentativo {attempt + 1} per: {keyword}")
            # Aumentiamo il timeout a 60 secondi
            response = requests.get("http://api.scraperapi.com", params=payload, timeout=60)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                results = []
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
                
                if results:
                    return results
                
            # Se arriviamo qui senza risultati o con errore status, aspettiamo un attimo
            time.sleep(2) 
            
        except requests.exceptions.Timeout:
            print(f"DEBUG - Timeout al tentativo {attempt + 1}, riprovo...")
            continue
        except Exception as e:
            return [{"title": "Errore Tecnico", "price": str(e), "source": "Etsy"}]

    return [{"title": "Timeout Persistente", "price": "Il server ScraperAPI è lento, riprova", "source": "Etsy"}]
