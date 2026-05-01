import os
import requests
import urllib.parse
from bs4 import BeautifulSoup

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

def run_lepe_scan(keyword: str, max_results: int = 15):
    try:
        if not SCRAPERAPI_KEY:
            return [{"title": "Errore", "price": "Chiave ScraperAPI mancante"}]

        # URL di ricerca Vinted Italia
        target_url = f"https://www.vinted.it/catalog?search_text={keyword.replace(' ', '+')}&order=newest_first"
        encoded_url = urllib.parse.quote(target_url)

        # Usiamo PREMIUM e RENDER per Vinted, altrimenti ci blocca subito
        final_api_url = f"http://api.scraperapi.com/?api_key={SCRAPERAPI_KEY}&url={encoded_url}&country_code=it&premium=true&render=true"
        
        print(f"DEBUG - Scansione Vinted per: {keyword}")

        response = requests.get(final_api_url, timeout=60)
        
        if response.status_code != 200:
            return [{"title": f"Errore {response.status_code}", "price": "ScraperAPI Block", "source": "Vinted"}]

        soup = BeautifulSoup(response.text, 'html.parser')
        results = []

        # Cerchiamo i contenitori dei prodotti su Vinted
        # Nota: Vinted usa classi che possono cambiare, usiamo selettori basati su attributi comuni
        items = soup.select('[data-testid^="grid-item"]')

        for item in items[:max_results]:
            # Estrazione Titolo (spesso nell'alt dell'immagine o in un titolo specifico)
            title_el = item.select_one('.feed-grid__item-title') or item.select_one('title')
            # Estrazione Prezzo
            price_el = item.select_one('[data-testid="grid-item-price"]') or item.find(text=lambda t: '€' in t)
            # Estrazione Link
            link_el = item.select_one('a[href*="/items/"]')

            if title_el and price_el:
                results.append({
                    "title": title_el.get_text(strip=True),
                    "price": price_el.get_text(strip=True),
                    "url": "https://www.vinted.it" + link_el['href'] if link_el else "N/D",
                    "source": "Vinted"
                })

        return results

    except Exception as e:
        return [{"title": "Errore Tecnico", "price": str(e), "source": "Vinted"}]
