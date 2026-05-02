import os
import asyncio
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI
from app.scraper.scanner import run_lepe_scan, run_scan_and_save
from app.scraper.notifier import run_notify_job

app = FastAPI(title="Lepefy Backend API")

# Keyword da monitorare con soglia prezzo massima
WATCH_LIST = [
    {"keyword": "ThinkPad", "threshold": 300},
    {"keyword": "Canon EOS", "threshold": 150},
]


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
    """Scansiona tutte le keyword in WATCH_LIST e salva i deal sotto soglia."""
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}

    results = []
    for watch in WATCH_LIST:
        result = await run_scan_and_save(watch["keyword"], watch["threshold"])
        results.append(result)
    return {"status": "ok", "results": results}


@app.get("/cron/notify")
async def cron_notify(secret: str = ""):
    """Invia email con i deal non ancora notificati agli utenti Premium."""
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_notify_job()

@app.get("/debug-supabase")
async def debug_supabase():
    import httpx
    url = os.getenv("SUPABASE_URL", "").rstrip("/")  # rimuove slash finale
    key = os.getenv("SUPABASE_SERVICE_KEY")
    full_url = f"{url}/rest/v1/scan_results?limit=1"
    
    async with httpx.AsyncClient() as client:
        r = await client.get(
            full_url,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
            }
        )
        return {
            "url_used": full_url,
            "status": r.status_code,
            "body": r.text
        }
