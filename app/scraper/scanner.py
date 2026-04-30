import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

async def run_lepe_scan(keyword: str):
    async with async_playwright() as p:
        # Avviamo Chromium con configurazioni per il server
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await stealth_async(page)
        
        # Testiamo su eBay (esempio: Nikon Z6)
        search_url = f"https://www.ebay.it/sch/i.html?_nkw={keyword.replace(' ', '+')}&_sop=12"
        await page.goto(search_url, wait_until="networkidle")
        
        # Estraiamo i primi 5 titoli e prezzi
        titles = await page.locator(".s-item__title").all_inner_texts()
        prices = await page.locator(".s-item__price").all_inner_texts()
        
        # Pulizia dati (prendiamo i primi 5 risultati ignorando i titoli vuoti)
        results = []
        for i in range(1, 6): # Partiamo da 1 perché il primo spesso è spazzatura
            if i < len(titles) and i < len(prices):
                results.append({
                    "title": titles[i],
                    "price": prices[i]
                })
        
        await browser.close()
        return results
