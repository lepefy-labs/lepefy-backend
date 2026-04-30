import os
import requests
from bs4 import BeautifulSoup

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

async def run_lepe_scan(keyword: str):
    if not SCRAPERAPI_KEY:
        return [{"title": "Errore: SCRAPERAPI_KEY mancante", "price": "0"}]

    target_url = f"https://www.ebay.it/sch/i.html?_nkw={keyword.replace(' ', '+')}"
    
    # Parametri per ScraperAPI: usiamo il rendering per sicurezza
    params = {
        'api_key': SCRAPERAPI_KEY,
        'url': target_url,
        'render': 'true' 
    }

    try:
        response = requests.get('http://api.scraperapi.com', params=params, timeout=60)
        
        if response.status_code != 200:
            return [{"title": f"Errore API: {response.status_code}", "price": "N/A"}]

        soup = BeautifulSoup(response.text, 'html.parser')
        results = []

        # Cerchiamo i contenitori dei prodotti
        # Usiamo selettori multipli perché eBay cambia spesso le classi
        items = soup.select('.s-item__info') or soup.select('.s-item__wrapper')

        for item in items[:15]:
            title_el = item.select_one('.s-item__title')
            price_el = item.select_one('.s-item__price')

            if title_el and price_el:
                title = title_el.get_text(strip=True).replace("Nuova inserzione", "")
                price = price_el.get_text(strip=True)
                
                # Escludiamo i risultati spazzatura di eBay
                if "Shop on eBay" not in title and title != "":
                    results.append({
                        "title": title,
                        "price": price,
                        "source": "eBay Professional"
                    })

        return results

    except Exception as e:
        print(f"Errore Scraper: {e}")
        return []
