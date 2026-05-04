import os
from fastapi import FastAPI
from app.scraper.scanner import run_lepe_scan, run_scan_and_save
from app.scraper.notifier import run_notify_job

app = FastAPI(title="Lepefy Backend API")

# Keyword da monitorare con soglia prezzo minima e massima
WATCH_LIST = [
    {"keyword": "ThinkPad",  "threshold": 300, "min_threshold": 80},
    {"keyword": "Canon EOS", "threshold": 400, "min_threshold": 40},
    {"keyword": "Nikon", "threshold": 300, "min_threshold": 40},
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
    """Scansiona tutte le keyword in WATCH_LIST e salva i deal tra soglia min e max."""
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}

    results = []
    for watch in WATCH_LIST:
        result = await run_scan_and_save(
            watch["keyword"],
            watch["threshold"],
            watch.get("min_threshold", 0),
        )
        results.append(result)
    return {"status": "ok", "results": results}


@app.get("/cron/notify")
async def cron_notify(secret: str = ""):
    """Invia email con i deal non ancora notificati agli utenti Premium."""
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_notify_job()
