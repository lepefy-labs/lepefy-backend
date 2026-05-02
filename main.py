@app.get("/debug-scan")
async def debug_scan(q: str = "ThinkPad"):
    import json, asyncio, requests
    from bs4 import BeautifulSoup

    def _debug_fetch():
        search_url = f"https://www.subito.it/annunci-italia/vendita/usato/?q={q}&sort=date_desc"
        params = {
            "api_key": os.getenv("SCRAPERAPI_KEY"),
            "url": search_url,
            "render": "true",
            "country_code": "it",
        }
        r = requests.get("http://api.scraperapi.com", params=params, timeout=60)
        soup = BeautifulSoup(r.text, "html.parser")
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag:
            return {"error": "__NEXT_DATA__ non trovato", "html_preview": r.text[:500]}
        data = json.loads(tag.string)
        # Ritorna solo la struttura delle chiavi, non i dati interi
        def map_keys(d, depth=4):
            if depth == 0: return "..."
            if isinstance(d, dict): return {k: map_keys(v, depth-1) for k, v in list(d.items())[:10]}
            if isinstance(d, list): return [map_keys(d[0], depth-1)] if d else []
            return type(d).__name__
        return map_keys(data)

    result = await asyncio.to_thread(_debug_fetch)
    return result
