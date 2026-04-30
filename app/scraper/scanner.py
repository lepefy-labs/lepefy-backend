import asyncio
from playwright.async_api import async_playwright
# Importiamo il modulo base invece della funzione specifica
import playwright_stealth 

async def run_lepe_scan(keyword: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, 
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        page = await context.new_page()
        
        # Tentativo di applicare lo stealth usando la funzione dal modulo
        try:
            # Nelle versioni recenti si usa spesso così:
            await playwright_stealth.stealth_async(page)
        except AttributeError:
            try:
                # Fallback per altre versioni della libreria
                from playwright_stealth import stealth_async
                await stealth_async(page)
            except Exception as e:
                print(f"Stealth non applicato: {e}. Procedo con i flag standard.")

        # Test su eBay
        search_url = f"https://www.ebay.it/sch/i.html?_nkw={keyword.replace(' ', '+')}"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        
        # Estrazione dati
        titles = await page.locator(".s-item__title").all_inner_texts()
        prices = await page.locator(".s-item__price").all_inner_texts()
        
        results = []
        for i in range(1, min(10, len(titles))):
            if titles[i].strip():
                results.append({
                    "title": titles[i].replace("Nuova inserzione", "").strip(),
                    "price": prices[i]
                })
        
        await browser.close()
        return results
