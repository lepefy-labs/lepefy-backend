import asyncio
from playwright.async_api import async_playwright

async def run_lepe_scan(keyword: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # Usiamo un sito "amichevole" per testare se la tecnologia funziona
        # In questo caso: Books to Scrape (un classico per i test)
        search_url = "https://books.toscrape.com/"
        
        await page.goto(search_url, wait_until="networkidle")
        
        # Estraiamo titoli e prezzi dei libri come se fossero annunci
        books = await page.locator(".product_pod").all()
        
        results = []
        for book in books[:10]:
            title = await book.locator("h3 a").get_attribute("title")
            price = await book.locator(".price_color").inner_text()
            
            # Filtriamo per keyword (simuliamo una ricerca)
            if keyword.lower() in title.lower() or keyword == "all":
                results.append({
                    "title": title,
                    "price": price
                })
        
        await browser.close()
        return results
