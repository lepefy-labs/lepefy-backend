import asyncio
from playwright.async_api import async_playwright
import playwright_stealth

async def run_lepe_scan(keyword: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, 
            args=[
                "--no-sandbox", 
                "--disable-setuid-sandbox", 
                "--disable-blink-features=AutomationControlled",
                "--use-gl=desktop" # Forza il rendering desktop
            ]
        )
        
        # Creiamo un contesto con impostazioni più realistiche
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080},
            extra_http_headers={
                "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": "https://www.google.com/"
            }
        )
        
        page = await context.new_page()
        
        try:
            await playwright_stealth.stealth_async(page)
        except:
            pass

        # URL di ricerca con parametro aggiuntivo per forzare la visualizzazione lista
        search_url = f"https://www.ebay.it/sch/i.html?_nkw={keyword.replace(' ', '+')}&_ipg=240"
        
        # Navigazione
        await page.goto(search_url, wait_until="networkidle", timeout=60000)
        
        # Aspettiamo che il selettore dei risultati sia effettivamente presente
        try:
            await page.wait_for_selector(".s-item__title", timeout=10000)
        except:
            await browser.close()
            return [] # Se non lo trova, restituisce vuoto senza crashare

        # Estrazione dati più precisa
        items = await page.locator(".s-item__wrapper-section").all()
        
        results = []
        for item in items[:10]: # Prendiamo i primi 10
            title_el = item.locator(".s-item__title")
            price_el = item.locator(".s-item__price")
            
            title = await title_el.inner_text()
            price = await price_el.inner_text()
            
            # Filtriamo i risultati inutili come "Annuncio sponsorizzato" o titoli vuoti
            if title and "Risultati per" not in title:
                results.append({
                    "title": title.replace("Nuova inserzione", "").strip(),
                    "price": price.strip()
                })
        
        await browser.close()
        return results
