import os
import asyncio
from playwright.async_api import async_playwright

# Recuperiamo la chiave dalle variabili d'ambiente di Railway
SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

async def run_lepe_scan(keyword: str):
    if not SCRAPERAPI_KEY:
        return {"error": "SCRAPERAPI_KEY non configurata su Railway"}

    async with async_playwright() as p:
        # Nota: Non serve passare proxy complessi nel browser, 
        # ScraperAPI gestisce tutto via URL
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()

        # Costruiamo l'URL di eBay
        target_url = f"https://www.ebay.it/sch/i.html?_nkw={keyword.replace(' ', '+')}"
        
        # Passiamo tramite ScraperAPI con render=true per gestire il JavaScript
        proxy_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={target_url}&render=true"

        print(f"Scansione in corso per: {keyword} tramite ScraperAPI...")
        
        try:
            # Tempo di attesa più lungo perché ScraperAPI deve ruotare i proxy
            await page.goto(proxy_url, timeout=90000)
            
            # Aspettiamo che i prodotti siano visibili
            await page.wait_for_selector(".s-item__title", timeout=20000)
            
            items = await page.locator(".s-item__wrapper-section").all()
            results = []

            for item in items[:10]:
                title = await item.locator(".s-item__title").inner_text()
                price = await item.locator(".s-item__price").inner_text()
                
                if title and "Risultati per" not in title:
                    results.append({
                        "title": title.replace("Nuova inserzione", "").strip(),
                        "price": price.strip(),
                        "source": "eBay via ScraperAPI"
                    })
            
            await browser.close()
            return results

        except Exception as e:
            await browser.close()
            print(f"Errore durante la scansione: {e}")
            return []
