import os
import requests
from bs4 import BeautifulSoup

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

async def run_lepe_scan(keyword: str):
    if not SCRAPERAPI_KEY:
        return [{"title": "Errore: Chiave Mancante", "price": "0"}]

    # URL per Subito.it (ordinato per i più recenti)
    target_url = f"https://www.subito.it/annunci-italia/vendita/usato/?q={keyword.replace(' ', '+')}&order=newest"
    
    params = {
        'api_key': SCRAPERAPI_KEY,
        'url': target_url,
        'render': 'true', # Subito carica i prezzi via JS, quindi serve render
        'country_code': 'it'
    }

    try:
        response = requests.get('http://api.scraperapi.com', params=params, timeout=60)
        soup = BeautifulSoup(response.text, 'html.parser')
        results = []

        # Selettore per le card di Subito.it
        # Cerchiamo i div che contengono le informazioni dell'annuncio
        items = soup.find_all("div", class_=lambda x: x and 'item-key-data' in x) or \
                soup.select('div[class*="SmallCard-module_card-contents"]')

        for item in items[:15]:
            # Subito usa classi che contengono nomi descrittivi
            title_el = item.find("h2") or item.select_one('h2[class*="ItemTitle"]')
            price_el = item.select_one('p[class*="price"]') or item.find("p", class_=lambda x: x and 'price' in x)

            if title_el:
                title = title_el.get_text(strip=True)
                price = price_el.get_text(strip=True) if price_el else "N/D"
                
                results.append({
                    "title": title,
                    "price": price,
                    "source": "Subito.it"
                })

        return results

    except Exception as e:
        return [{"title": "Errore Tecnico", "price": str(e)}]
