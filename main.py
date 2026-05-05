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
    import httpx
    api_key = os.getenv("SCRAPERAPI_KEY")
    target = "https://www.subito.it/hades/v1/search/items/?q=ThinkPad&lim=3&sort=datedesc"
    import urllib.parse
    r = httpx.get(
        f"http://api.scraperapi.com/?api_key={api_key}&url={urllib.parse.quote(target)}&country_code=it",
        timeout=30
    )
    return {"status": r.status_code, "body": r.text[:1000]}
