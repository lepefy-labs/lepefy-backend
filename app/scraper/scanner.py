import os
import requests
from bs4 import BeautifulSoup

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

async def run_lepe_scan(keyword: str):
    if not SCRAPERAPI_KEY:
        return [{"title": "Errore: Chiave Mancante", "price": "0"}]

    # Proviamo eBay.com (spesso più permissivo con i proxy rispetto a .it)
    target_url = f"https://www.ebay.com/sch/i.html?_nkw={keyword.replace(' ', '+')}"
    
    params = {
        'api_key': SCRAPERAPI_KEY,
        'url': target_url,
        'render': 'false', # Proviamo 'false' per evitare conflitti di rendering JS
        'country_code': 'it' # Chiediamo a ScraperAPI di usare un IP italiano
    }

    try:
        response = requests.get('http://api.scraperapi.com', params=params, timeout=60)
        
        # DEBUG: Stampiamo i primi 500 caratteri dell'HTML nei log di Railway
        print(f"HTML Preview: {response.text[:500]}")

        soup = BeautifulSoup(response.text, 'html.parser')
        results = []

        # eBay usa spesso questi selettori per i titoli:
        # 1. .s-item__title
        # 2. h3.s-item__title
        items = soup.find_all("div", class_="s-item__info")

        for item in items:
            title_el = item.find("span", role="heading") or item.find("div", class_="s-item__title")
            price_el = item.find("span", class_="s-item__price")

            if title_el and price_el:
                title = title_el.get_text(strip=True)
                price = price_el.get_text(strip=True)
                
                if "Shop on eBay" not in title and len(title) > 5:
                    results.append({
                        "title": title,
                        "price": price,
                        "source": "eBay Debug"
                    })

        # Se ancora vuoto, proviamo un selettore ultra-generico
        if not results:
            titles = soup.select(".s-item__title")
            for t in titles[:5]:
                results.append({"title": t.get_text(), "price": "Check Log"})

        return results

    except Exception as e:
        return [{"title": "Errore Exception", "price": str(e)}]
