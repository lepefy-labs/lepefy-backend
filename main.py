import os
import hashlib
from fastapi import FastAPI, Request
from app.scraper.scanner import run_lepe_scan, run_scan_and_save
from app.scraper.scorer import run_score_job
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
    Fetch Subito per ogni keyword attiva e salva annunci grezzi (scored=false).
    Nessuna chiamata AI o eBay — veloce e resiliente.
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_scan_and_save()


@app.get("/cron/score")
async def cron_score(secret: str = ""):
    """
    Legge annunci con scored=false, chiama eBay + Claude Haiku,
    aggiorna score e margine in scan_results.
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_score_job()


@app.get("/cron/notify")
async def cron_notify(secret: str = ""):
    """
    Per ogni subscription attiva, invia i deal scored=true
    non ancora notificati che rientrano nella fascia prezzo.
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
    return get_price_stats(modello=modello, condizione=condizione, giorni=giorni)


@app.get("/market/time-to-sell")
async def market_time_to_sell(
    modello: str,
    condizione: str | None = None,
    giorni: int = 180,
):
    return get_time_to_sell(modello=modello, condizione=condizione, giorni=giorni)


@app.get("/market/price-trend")
async def market_price_trend(
    modello: str,
    giorni: int = 90,
    bucket: str = "week",
):
    return get_price_trend(modello=modello, giorni=giorni, bucket=bucket)


@app.get("/market/active")
async def market_active_listings(
    categoria: str | None = None,
    marca: str | None = None,
    modello: str | None = None,
    condizione: str | None = None,
    limit: int = 50,
):
    return get_active_listings(
        categoria=categoria,
        marca=marca,
        modello=modello,
        condizione=condizione,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# eBay Marketplace Account Deletion (GDPR compliance)
# ---------------------------------------------------------------------------

@app.get("/ebay/account-deletion")
async def ebay_account_deletion_challenge(challenge_code: str = ""):
    """
    Validazione endpoint eBay.
    SHA256(challenge_code + verification_token + endpoint_url)
    """
    if challenge_code:
        verification_token = os.getenv("EBAY_VERIFICATION_TOKEN", "")
        endpoint = "https://lepefy-backend-production.up.railway.app/ebay/account-deletion"
        hash_input = challenge_code + verification_token + endpoint
        challenge_response = hashlib.sha256(hash_input.encode()).hexdigest()
        return {"challengeResponse": challenge_response}
    return {"ack": "Success"}


@app.post("/ebay/account-deletion")
async def ebay_account_deletion(request: Request):
    """Riceve notifiche di cancellazione account eBay (GDPR)."""
    return {"ack": "Success"}


# ---------------------------------------------------------------------------
# Debug (da rimuovere in produzione)
# ---------------------------------------------------------------------------

from test_vinted import run_tests

@app.get("/test-vinted")
async def test_vinted_endpoint():
    return run_tests()

@app.get("/debug/scraperapi")
async def debug_scraperapi():
    import httpx
    api_key = os.getenv("SCRAPERAPI_KEY")
    r = httpx.get(f"http://api.scraperapi.com/account?api_key={api_key}", timeout=10)
    return r.json()


@app.get("/debug/static")
async def debug_static():
    import httpx
    api_key = os.getenv("SCRAPERAPI_KEY")
    r = httpx.get(
        "http://api.scraperapi.com/",
        params={"api_key": api_key,
                "url": "https://www.subito.it/annunci-italia/vendita/usato/?q=ThinkPad"},
        timeout=30,
    )
    html = r.text
    marker = "__NEXT_DATA__"
    found = marker in html
    preview = ""
    if found:
        start = html.find(marker)
        preview = html[start: start + 200]
    return {
        "http_status": r.status_code,
        "next_data_found": found,
        "preview": preview,
        "html_length": len(html),
    }
