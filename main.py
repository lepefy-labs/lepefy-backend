import os
from fastapi import FastAPI
from app.scraper.scanner import run_lepe_scan, run_scan_and_save
from app.scraper.notifier import run_notify_job

app = FastAPI(title="Lepefy Backend API")


@app.get("/")
def read_root():
    return {"message": "Welcome to Lepefy API - Connection Active"}


@app.get("/test-scan")
async def test_scan(q: str = "ThinkPad"):
    """Scansiona e ritorna gli annunci senza filtrare né salvare."""
    try:
        data = await run_lepe_scan(q)
        return {"status": "success", "keyword": q, "found_items": data}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/cron/scan")
async def cron_scan(secret: str = ""):
    """
    Legge le keyword attive da Supabase, scansiona ciascuna una volta sola
    e salva i deal nel pool condiviso scan_results.
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_scan_and_save()


@app.get("/cron/notify")
async def cron_notify(secret: str = ""):
    """
    Per ogni subscription attiva, invia i deal non ancora notificati
    che rientrano nella fascia prezzo dell'utente.
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_notify_job()

@app.get("/debug/hades")
async def debug_hades():
    import httpx, urllib.parse, os
    api_key = os.getenv("SCRAPERAPI_KEY")
    target = "https://www.subito.it/hades/v1/search/items/?q=ThinkPad&lim=3&sort=datedesc"
    r = httpx.get(
        f"http://api.scraperapi.com/",
        params={
            "api_key": api_key,
            "url": target,
            "country_code": "it",
            "keep_headers": "true",
        },
        headers={
            "Origin": "https://www.subito.it",
            "Referer": "https://www.subito.it/annunci-italia/vendita/usato/?q=ThinkPad",
            "Accept": "application/json",
            "x-source": "subito-ui",
        },
        timeout=30
    )
    return {"status": r.status_code, "body": r.text[:1000]}

@app.get("/debug/static")
async def debug_static():
    import httpx, os, json
    api_key = os.getenv("SCRAPERAPI_KEY")
    r = httpx.get(
        "http://api.scraperapi.com/",
        params={
            "api_key": api_key,
            "url": "https://www.subito.it/annunci-italia/vendita/usato/?q=ThinkPad",
            "country_code": "it",
        },
        timeout=30
    )
    # Cerca __NEXT_DATA__ con stringa semplice
    html = r.text
    marker = '__NEXT_DATA__'
    found = marker in html
    preview = ""
    if found:
        start = html.find(marker)
        preview = html[start:start+200]
    return {
        "http_status": r.status_code,
        "next_data_found": found,
        "preview": preview,
        "html_length": len(html),
    }
