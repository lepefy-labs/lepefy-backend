import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

async def run_lepe_scan(keyword: str):
    async with async_playwright() as p:
        # Avvio browser con flag per evitare il rilevamento
        browser = await p.chromium.launch(
            headless=True, 
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        page = await context.new_page()
        
        # Applichiamo lo stealth
        # Se stealth_async dà ancora problemi, usiamo la versione sincrona che spesso è inclusa diversamente
        try:
            await stealth_async(page)
        except Exception:
            # Fallback: lo scanner continuerà comunque con i flag del browser sopra
            print("Stealth_async non disponibile, procedo con i flag standard")

        # Test su eBay
        search_url = f"https://www.ebay.it/sch/i.html?_nkw={keyword.replace(' ', '+')}"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        
        # Estrazione dati
        titles = await page.locator(".s-item__title").all_inner_texts()
        prices = await page.locator(".s-item__price").all_inner_texts()
        
        results = []
        # Saltiamo il primo elemento (spesso un placeholder di eBay)
        for i in range(1, min(6, len(titles))):
            results.append({
                "title": titles[i].replace("Nuova inserzione", "").strip(),
                "price": prices[i]
            })
        
        await browser.close()
        return results
