import os
import hashlib
from fastapi import FastAPI, Request, BackgroundTasks
from app.scraper.scanner          import run_lepe_scan, run_scan_and_save
from app.scraper.scorer           import run_score_job
from app.scraper.notifier         import run_notify_job
from app.scraper.market_scanner   import run_market_scan
from app.scraper.market_analytics import (
    get_price_stats,
    get_time_to_sell,
    get_price_trend,
    get_active_listings,
)
from app.scraper.vinted_scanner           import run_vinted_scan
from app.scraper.vinted_defective_scanner import run_vinted_defective_scan
from app.scraper.notifier_defective       import run_defective_notify_job
from app.scraper.vinted_collector_scanner import run_vinted_collector_scan
from app.scraper.notifier_collector       import run_collector_notify_job
from app.scraper.content_generator        import run_content_job
from app.scraper.availability_checker     import run_availability_check, run_availability_test

app = FastAPI(title="Lepefy Backend API")

from app.routers.unsubscribe import router as unsubscribe_router
app.include_router(unsubscribe_router)
from app.routers.app_api     import router as app_router
app.include_router(app_router)


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


@app.get("/cron/vinted-scan")
async def cron_vinted_scan(secret: str = ""):
    """
    Fetch Vinted.it per ogni keyword attiva e salva annunci grezzi (scored=false).
    Lo scorer esistente li raccoglierà al ciclo successivo senza modifiche.
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_vinted_scan()


@app.get("/cron/vinted-defective-scan")
async def cron_vinted_defective_scan(secret: str = ""):
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_vinted_defective_scan()


@app.get("/cron/score")
async def cron_score(secret: str = ""):
    """
    Legge annunci con scored=false, chiama Claude Haiku,
    aggiorna score e margine in scan_results.
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_score_job()


@app.get("/cron/notify")
async def cron_notify(secret: str = ""):
    """
    Notifica flipper (Subito + Vinted) con annunci score >= 7.
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_notify_job()


@app.get("/cron/notify-defective")
async def cron_notify_defective(secret: str = ""):
    """
    Notifica riparatori con annunci difettosi non ancora notificati.
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_defective_notify_job()


@app.get("/cron/scan-vinted-collector")
async def cron_scan_vinted_collector(secret: str = ""):
    """
    Fetch Vinted per keyword collezionisti (only_collector=true).
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_vinted_collector_scan()


@app.get("/cron/notify-collector")
async def cron_notify_collector(secret: str = ""):
    """
    Notifica collezionisti con annunci non ancora notificati.
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_collector_notify_job()


# ---------------------------------------------------------------------------
# Availability checker
# ---------------------------------------------------------------------------

@app.get("/cron/check-availability")
async def cron_check_availability(
    secret: str = "",
    background_tasks: BackgroundTasks = None,
):
    """
    Verifica disponibilità annunci in scan_results partendo dai record DB.
    Marca is_sold=true gli annunci rimossi/venduti su Subito e Vinted.
    Ritorna 202 immediatamente — il job gira in background per evitare
    il timeout HTTP di Railway (batch da 100 URL può durare 5+ minuti).
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    background_tasks.add_task(run_availability_check)
    return {"status": "accepted", "message": "Availability check avviato in background"}


@app.get("/test/check-availability")
async def test_check_availability(
    secret:  str  = "",
    limit:   int  = 5,
    source:  str  = "all",
    dry_run: bool = True,
):
    """
    Verifica `limit` annunci (max 20) e ritorna il dettaglio record per record.

    Parametri:
      limit    — quanti annunci controllare (1–20, default 5)
      source   — "subito", "vinted", "all" (default "all")
      dry_run  — true: solo log, nessuna scrittura DB (default true)
                 false: scrive is_sold/sold_at/last_checked_at

    Esempio:
      GET /test/check-availability?secret=TOKEN&limit=5&source=subito&dry_run=true
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return await run_availability_test(limit=limit, source=source, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Content generator
# ---------------------------------------------------------------------------

@app.get("/cron/content")
async def cron_content(secret: str = ""):
    """
    Genera contenuti social dai top deal scorati.
    """
    if secret != os.getenv("CRON_SECRET"):
        return {"error": "unauthorized"}
    return run_content_job()


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
    modello:    str,
    condizione: str | None = None,
    giorni:     int = 90,
):
    return get_price_stats(modello=modello, condizione=condizione, giorni=giorni)


@app.get("/market/time-to-sell")
async def market_time_to_sell(
    modello:    str,
    condizione: str | None = None,
    giorni:     int = 180,
):
    return get_time_to_sell(modello=modello, condizione=condizione, giorni=giorni)


@app.get("/market/price-trend")
async def market_price_trend(
    modello: str,
    giorni:  int = 90,
    bucket:  str = "week",
):
    return get_price_trend(modello=modello, giorni=giorni, bucket=bucket)


@app.get("/market/active")
async def market_active_listings(
    categoria:  str | None = None,
    marca:      str | None = None,
    modello:    str | None = None,
    condizione: str | None = None,
    limit:      int = 50,
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
