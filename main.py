import os
from app.scraper.content_generator import run_content_job
from fastapi import FastAPI
from fastapi import FastAPI, HTTPException, Query
from app.scraper.scanner import run_lepe_scan, run_scan_and_save
from app.scraper.notifier import run_notify_job
from app.scraper.market_scanner import run_market_scan
from app.scraper.market_analytics import (
    get_price_stats,
    get_time_to_sell,
    get_price_trend,
    get_active_listings,
)

app = FastAPI(title="Lepefy Backend API")


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

@app.get("/")
def read_root():
    return {"message": "Welcome to Lepefy API - Connection Active"}


# ---------------------------------------------------------------------------
# Scanner operativo
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Market scanner
# ---------------------------------------------------------------------------

@app.get("/cron/market-scan")
async def cron_market_scan(secret: str = ""):
    """
    Scansiona la tassonomia fissa di mercato e aggiorna market_snapshots.
    Suggerita frequenza: 1-2 volte al giorno.
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_market_scan()


# ---------------------------------------------------------------------------
# Market analytics
# ---------------------------------------------------------------------------

@app.get("/market/price-stats")
async def market_price_stats(
    modello: str,
    condizione: str | None = None,
    giorni: int = 90,
):
    """
    Statistiche di prezzo per un modello.
    Esempio: /market/price-stats?modello=A7 III&condizione=Ottime condizioni
    """
    return get_price_stats(modello=modello, condizione=condizione, giorni=giorni)


@app.get("/market/time-to-sell")
async def market_time_to_sell(
    modello: str,
    condizione: str | None = None,
    giorni: int = 180,
):
    """
    Tempo medio di vendita per un modello.
    Esempio: /market/time-to-sell?modello=PS5
    """
    return get_time_to_sell(modello=modello, condizione=condizione, giorni=giorni)


@app.get("/market/price-trend")
async def market_price_trend(
    modello: str,
    giorni: int = 90,
    bucket: str = "week",
):
    """
    Andamento del prezzo mediano nel tempo.
    bucket: day | week | month
    Esempio: /market/price-trend?modello=A7 III&bucket=week
    """
    return get_price_trend(modello=modello, giorni=giorni, bucket=bucket)


@app.get("/market/active")
async def market_active_listings(
    categoria: str | None = None,
    marca: str | None = None,
    modello: str | None = None,
    condizione: str | None = None,
    limit: int = 50,
):
    """
    Annunci attivi filtrabili per categoria, marca, modello, condizione.
    Esempio: /market/active?categoria=fotografia&marca=Sony
    """
    return get_active_listings(
        categoria=categoria,
        marca=marca,
        modello=modello,
        condizione=condizione,
        limit=limit,
    )

@app.get("/cron/content")
async def cron_content(secret: str = Query(...)):
    if secret != os.environ.get("CRON_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    result = run_content_job()
    return result

# ---------------------------------------------------------------------------
# Debug (da rimuovere in produzione)
# ---------------------------------------------------------------------------

@app.get("/debug/hades")
async def debug_hades():
    import httpx
    api_key = os.getenv("SCRAPERAPI_KEY")
    target = "https://www.subito.it/hades/v1/search/items/?q=ThinkPad&lim=3&sort=datedesc"
    r = httpx.get(
        "http://api.scraperapi.com/",
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
        timeout=30,
    )
    return {"status": r.status_code, "body": r.text[:1000]}


@app.get("/debug/static")
async def debug_static():
    import httpx
    api_key = os.getenv("SCRAPERAPI_KEY")
    r = httpx.get(
        "http://api.scraperapi.com/",
        params={
            "api_key": api_key,
            "url": "https://www.subito.it/annunci-italia/vendita/usato/?q=ThinkPad",
            "country_code": "it",
        },
        timeout=30,
    )
    html = r.text
    marker = "__NEXT_DATA__"
    found = marker in html
    preview = ""
    if found:
        start = html.find(marker)
        preview = html[start : start + 200]
    return {
        "http_status":    r.status_code,
        "next_data_found": found,
        "preview":        preview,
        "html_length":    len(html),
    }
