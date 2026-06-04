"""
availability_checker.py — Verifica disponibilità annunci in scan_results.

Logica inversa rispetto agli scanner:
  DB → legge URL attivi (is_sold=false) → HTTP check → aggiorna is_sold/sold_at

Strategia di priorità (ordine di esecuzione per batch):
  1. Score >= 7 e notificati → annunci che qualcuno sta ancora guardando
  2. created_at < 7 giorni, mai controllati (last_checked_at IS NULL)
  3. Tutto il resto ordinato per last_checked_at ASC (i più vecchi prima)

Subito.it  → fetch via ScraperAPI → parse __NEXT_DATA__ → cerca url nell'annuncio
            Se 404 / redirect / annuncio non trovato → is_sold = true
Vinted.it  → GET /api/v2/items/{id} via sessione cookie
            Se 404 / can_be_sold=false / status=sold → is_sold = true

Prerequisiti DB:
    ALTER TABLE scan_results
    ADD COLUMN IF NOT EXISTS is_sold         boolean     DEFAULT false,
    ADD COLUMN IF NOT EXISTS sold_at         timestamptz DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS last_checked_at timestamptz DEFAULT NULL;

    CREATE INDEX IF NOT EXISTS idx_scan_results_is_sold
      ON scan_results (is_sold) WHERE is_sold = false;
"""

import os
import re
import json
import time
import asyncio
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from supabase import create_client, Client

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
SCRAPERAPI_KEY       = os.getenv("SCRAPERAPI_KEY")
SCRAPERAPI_URL       = "http://api.scraperapi.com"

VINTED_HOME = "https://www.vinted.it"
VINTED_ITEM_API = f"{VINTED_HOME}/api/v2/items"

# Annunci da verificare per run
BATCH_SIZE = 100

HEADERS_VINTED_HOME = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}
HEADERS_VINTED_API = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": "https://www.vinted.it/catalog",
    "X-Requested-With": "XMLHttpRequest",
}


# ──────────────────────────────────────────────
# Supabase
# ──────────────────────────────────────────────

def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────
# Selezione batch con priorità
# ──────────────────────────────────────────────

def _fetch_batch(supabase: Client) -> list[dict]:
    """
    Legge fino a BATCH_SIZE record da controllare, in ordine di priorità:
      P1 — score >= 7, notificati (presenti in notifications_log)
      P2 — created_at < 7gg, last_checked_at IS NULL
      P3 — tutto il resto, last_checked_at ASC
    """
    results: list[dict] = []
    seen_ids: set[str] = set()

    def _add(rows: list[dict]) -> None:
        for r in rows:
            if r["id"] not in seen_ids and len(results) < BATCH_SIZE:
                seen_ids.add(r["id"])
                results.append(r)

    cols = "id, url, source, last_checked_at, score"

    # P1 — score >= 7 notificati
    notified_ids_resp = (
        supabase.table("notifications_log")
        .select("scan_result_id")
        .execute()
    )
    notified_ids = list({
        r["scan_result_id"]
        for r in (notified_ids_resp.data or [])
        if r.get("scan_result_id")
    })

    if notified_ids:
        # Supabase non supporta .in_() con lista vuota — guard già presente
        p1 = (
            supabase.table("scan_results")
            .select(cols)
            .eq("is_sold", False)
            .gte("score", 7)
            .in_("id", notified_ids[:500])          # cap per sicurezza
            .order("last_checked_at", desc=False, nullsfirst=True)
            .limit(BATCH_SIZE)
            .execute()
        )
        _add(p1.data or [])

    # P2 — recenti mai controllati
    if len(results) < BATCH_SIZE:
        seven_days_ago = (
            datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
        )
        p2 = (
            supabase.table("scan_results")
            .select(cols)
            .eq("is_sold", False)
            .is_("last_checked_at", "null")
            .gte("created_at", seven_days_ago)
            .order("created_at", desc=False)
            .limit(BATCH_SIZE - len(results))
            .execute()
        )
        _add(p2.data or [])

    # P3 — backlog generale
    if len(results) < BATCH_SIZE:
        p3 = (
            supabase.table("scan_results")
            .select(cols)
            .eq("is_sold", False)
            .order("last_checked_at", desc=False, nullsfirst=True)
            .limit(BATCH_SIZE - len(results))
            .execute()
        )
        _add(p3.data or [])

    return results


# ──────────────────────────────────────────────
# Check Subito.it
# ──────────────────────────────────────────────

def _extract_item_id_subito(url: str) -> str | None:
    """Estrae l'ID numerico dall'URL Subito es: /annunci/123456789.htm → 123456789"""
    m = re.search(r"/(\d+)\.htm", url)
    return m.group(1) if m else None


def _check_subito(url: str) -> bool:
    """
    Ritorna True se l'annuncio è ancora disponibile, False se venduto/rimosso.

    Strategia:
      1. Fetch pagina via ScraperAPI
      2. Se status_code != 200 → venduto
      3. Parse __NEXT_DATA__ → cerca l'item_id nei dati
         - Se non trovato o campo "status" assente → consideriamo disponibile
           (pagina cambiata ma non siamo sicuri)
         - Se "status" contiene "sold" o "removed" → venduto
      4. Fallback: se il titolo HTML contiene "annuncio non trovato" → venduto
    """
    item_id = _extract_item_id_subito(url)
    try:
        params = {"api_key": SCRAPERAPI_KEY, "url": url}
        r = requests.get(SCRAPERAPI_URL, params=params, timeout=60)

        # 404 o redirect a ricerca = certamente rimosso
        if r.status_code == 404:
            return False
        if r.status_code != 200:
            # Errore transitorio — non marchiamo come venduto
            return True

        html = r.text

        # Fallback testuale rapido prima del parsing JSON
        html_lower = html.lower()
        if "annuncio non trovato" in html_lower or "annuncio non disponibile" in html_lower:
            return False

        # Parse __NEXT_DATA__
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag:
            # ScraperAPI ha restituito qualcosa ma senza __NEXT_DATA__
            # Potrebbe essere un errore transitorio — non marchiamo
            return True

        try:
            data = json.loads(tag.string)
        except json.JSONDecodeError:
            return True

        # Cerca l'annuncio nei dati della pagina
        page_props = data.get("props", {}).get("pageProps", {})

        # Struttura pagina singolo annuncio: pageProps.adItem o pageProps.item
        ad = page_props.get("adItem") or page_props.get("item") or {}

        if not ad and item_id:
            # Cerca ricorsivamente l'id nell'albero JSON (fallback)
            raw = json.dumps(data)
            if item_id not in raw:
                return False  # ID non trovato nei dati → rimosso

        # Campo status esplicito
        status = str(ad.get("status", "")).lower()
        if status in ("sold", "removed", "expired", "deleted"):
            return False

        # Flag sold esplicito (alcune versioni API)
        if ad.get("is_sold") or ad.get("sold"):
            return False

        return True

    except requests.exceptions.Timeout:
        # Timeout → saltiamo, non marchiamo come venduto
        return True
    except Exception as e:
        print(f"[checker] Subito check error {url}: {e}")
        return True  # in caso di errore non marchiamo


# ──────────────────────────────────────────────
# Check Vinted.it
# ──────────────────────────────────────────────

def _extract_item_id_vinted(url: str) -> str | None:
    """
    Estrae l'ID numerico dall'URL Vinted.
    Formati:
      /items/1234567890-titolo-annuncio
      /it/items/1234567890
    """
    m = re.search(r"/items/(\d+)", url)
    return m.group(1) if m else None


def _get_vinted_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS_VINTED_HOME)
    r = session.get(VINTED_HOME, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Vinted home returned {r.status_code}")
    session.headers.update(HEADERS_VINTED_API)
    return session


def _check_vinted(session: requests.Session, url: str) -> bool:
    """
    Ritorna True se disponibile, False se venduto/rimosso.

    Usa GET /api/v2/items/{id}:
      - 404 → rimosso
      - item.can_be_sold == false → venduto/riservato
      - item.status in ("sold", "reserved", "hidden") → non disponibile
    """
    item_id = _extract_item_id_vinted(url)
    if not item_id:
        # URL non parsabile — saltiamo
        return True

    try:
        r = session.get(f"{VINTED_ITEM_API}/{item_id}", timeout=20)

        if r.status_code == 404:
            return False

        if r.status_code == 401:
            # Sessione scaduta — non marchiamo
            raise RuntimeError("Vinted session expired")

        if r.status_code != 200:
            return True  # errore transitorio

        data = r.json()
        item = data.get("item", {})

        if not item:
            return False

        # can_be_sold è il campo più affidabile
        can_be_sold = item.get("can_be_sold")
        if can_be_sold is False:
            return False

        status = str(item.get("status", "")).lower()
        if status in ("sold", "reserved", "hidden", "disabled"):
            return False

        return True

    except RuntimeError:
        raise  # rilanciamo per gestire session refresh nel chiamante
    except requests.exceptions.Timeout:
        return True
    except Exception as e:
        print(f"[checker] Vinted check error {url}: {e}")
        return True


# ──────────────────────────────────────────────
# Job principale
# ──────────────────────────────────────────────

def _run_check_job() -> dict:
    supabase = _get_supabase()
    now = _now()

    records = _fetch_batch(supabase)
    if not records:
        return {"status": "ok", "message": "Nessun annuncio da verificare", "checked": 0}

    # Separa per source
    subito_records = [r for r in records if "subito" in r.get("source", "").lower()]
    vinted_records = [r for r in records if "vinted" in r.get("source", "").lower()]

    sold_count    = 0
    checked_count = 0
    errors        = 0

    # ── Subito ──────────────────────────────────
    for rec in subito_records:
        url = rec.get("url", "")
        rec_id = rec["id"]
        if not url:
            continue

        try:
            available = _check_subito(url)
        except Exception as e:
            print(f"[checker] Subito error id={rec_id}: {e}")
            errors += 1
            # Aggiorna comunque last_checked_at per non riprocesare subito
            supabase.table("scan_results").update(
                {"last_checked_at": now}
            ).eq("id", rec_id).execute()
            time.sleep(1)
            continue

        update = {"last_checked_at": now}
        if not available:
            update["is_sold"] = True
            update["sold_at"] = now
            sold_count += 1

        supabase.table("scan_results").update(update).eq("id", rec_id).execute()
        checked_count += 1
        time.sleep(1.5)  # ScraperAPI rate limit

    # ── Vinted ──────────────────────────────────
    if vinted_records:
        try:
            session = _get_vinted_session()
        except Exception as e:
            print(f"[checker] Vinted session failed: {e}")
            errors += len(vinted_records)
            return {
                "status": "partial",
                "checked": checked_count,
                "sold": sold_count,
                "errors": errors,
                "note": "Vinted session failed — Subito completato",
            }

        for rec in vinted_records:
            url = rec.get("url", "")
            rec_id = rec["id"]
            if not url:
                continue

            try:
                available = _check_vinted(session, url)
            except RuntimeError:
                # Sessione scaduta — tenta refresh una volta
                try:
                    session = _get_vinted_session()
                    available = _check_vinted(session, url)
                except Exception as e2:
                    print(f"[checker] Vinted session refresh failed: {e2}")
                    errors += 1
                    continue
            except Exception as e:
                print(f"[checker] Vinted error id={rec_id}: {e}")
                errors += 1
                supabase.table("scan_results").update(
                    {"last_checked_at": now}
                ).eq("id", rec_id).execute()
                time.sleep(0.5)
                continue

            update = {"last_checked_at": now}
            if not available:
                update["is_sold"] = True
                update["sold_at"] = now
                sold_count += 1

            supabase.table("scan_results").update(update).eq("id", rec_id).execute()
            checked_count += 1
            time.sleep(0.5)

    return {
        "status":  "ok",
        "checked": checked_count,
        "sold":    sold_count,
        "errors":  errors,
        "subito":  len(subito_records),
        "vinted":  len(vinted_records),
    }


async def run_availability_check() -> dict:
    try:
        return await asyncio.to_thread(_run_check_job)
    except Exception as e:
        return {"status": "error", "detail": str(e)}
