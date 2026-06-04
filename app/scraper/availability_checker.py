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

VINTED_HOME     = "https://www.vinted.it"
VINTED_ITEM_API = f"{VINTED_HOME}/api/v2/items"

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

def _fetch_batch(
    supabase: Client,
    limit: int = BATCH_SIZE,
    source_filter: str | None = None,   # "subito", "vinted", None = tutti
) -> list[dict]:
    """
    Legge fino a `limit` record da controllare, in ordine di priorità:
      P1 — score >= 7, notificati (presenti in notifications_log)
      P2 — created_at < 7gg, last_checked_at IS NULL
      P3 — tutto il resto, last_checked_at ASC
    """
    results: list[dict] = []
    seen_ids: set[str] = set()

    def _add(rows: list[dict]) -> None:
        for r in rows:
            if r["id"] not in seen_ids and len(results) < limit:
                seen_ids.add(r["id"])
                results.append(r)

    def _apply_source(q):
        if source_filter == "subito":
            return q.ilike("source", "%subito%")
        if source_filter == "vinted":
            return q.ilike("source", "%vinted%")
        return q

    cols = "id, url, source, last_checked_at, score, keyword, title, price_value"

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
        q = (
            supabase.table("scan_results")
            .select(cols)
            .eq("is_sold", False)
            .gte("score", 7)
            .in_("id", notified_ids[:500])
            .order("last_checked_at", desc=False, nullsfirst=True)
            .limit(limit)
        )
        _add((_apply_source(q).execute()).data or [])

    # P2 — recenti mai controllati
    if len(results) < limit:
        seven_days_ago = (
            datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
        )
        q = (
            supabase.table("scan_results")
            .select(cols)
            .eq("is_sold", False)
            .is_("last_checked_at", "null")
            .gte("created_at", seven_days_ago)
            .order("created_at", desc=False)
            .limit(limit - len(results))
        )
        _add((_apply_source(q).execute()).data or [])

    # P3 — backlog generale
    if len(results) < limit:
        q = (
            supabase.table("scan_results")
            .select(cols)
            .eq("is_sold", False)
            .order("last_checked_at", desc=False, nullsfirst=True)
            .limit(limit - len(results))
        )
        _add((_apply_source(q).execute()).data or [])

    return results


# ──────────────────────────────────────────────
# Check Subito.it
# ──────────────────────────────────────────────

def _extract_item_id_subito(url: str) -> str | None:
    m = re.search(r"/(\d+)\.htm", url)
    return m.group(1) if m else None


def _check_subito(url: str) -> tuple[bool, str]:
    """
    Ritorna (available: bool, reason: str).
    reason descrive l'esito per il dry_run log.
    """
    item_id = _extract_item_id_subito(url)
    try:
        params = {"api_key": SCRAPERAPI_KEY, "url": url}
        r = requests.get(SCRAPERAPI_URL, params=params, timeout=60)

        if r.status_code in (404, 410):
            return False, f"http_{r.status_code}"
        if r.status_code != 200:
            return True, f"http_{r.status_code}_transitorio"

        html = r.text
        html_lower = html.lower()
        if "annuncio non trovato" in html_lower or "annuncio non disponibile" in html_lower:
            return False, "testo_non_trovato"

        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag:
            return True, "next_data_mancante_transitorio"

        try:
            data = json.loads(tag.string)
        except json.JSONDecodeError:
            return True, "json_parse_error_transitorio"

        page_props = data.get("props", {}).get("pageProps", {})
        ad = page_props.get("adItem") or page_props.get("item") or {}

        if not ad and item_id:
            raw = json.dumps(data)
            if item_id not in raw:
                return False, "id_non_presente_nel_json"

        status = str(ad.get("status", "")).lower()
        if status in ("sold", "removed", "expired", "deleted"):
            return False, f"status_{status}"

        if ad.get("is_sold") or ad.get("sold"):
            return False, "flag_sold"

        return True, "disponibile"

    except requests.exceptions.Timeout:
        return True, "timeout_transitorio"
    except Exception as e:
        return True, f"errore_{str(e)[:60]}"


# ──────────────────────────────────────────────
# Check Vinted.it
# ──────────────────────────────────────────────

def _extract_item_id_vinted(url: str) -> str | None:
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


def _check_vinted(session: requests.Session, url: str) -> tuple[bool, str]:
    """
    Ritorna (available: bool, reason: str).
    """
    item_id = _extract_item_id_vinted(url)
    if not item_id:
        return True, "url_non_parsabile"

    try:
        r = session.get(f"{VINTED_ITEM_API}/{item_id}", timeout=20)

        if r.status_code == 404:
            return False, "http_404"
        if r.status_code == 401:
            raise RuntimeError("Vinted session expired")
        if r.status_code != 200:
            return True, f"http_{r.status_code}_transitorio"

        data = r.json()
        item = data.get("item", {})

        if not item:
            return False, "item_vuoto"

        can_be_sold = item.get("can_be_sold")
        if can_be_sold is False:
            return False, "can_be_sold_false"

        status = str(item.get("status", "")).lower()
        if status in ("sold", "reserved", "hidden", "disabled"):
            return False, f"status_{status}"

        return True, "disponibile"

    except RuntimeError:
        raise
    except requests.exceptions.Timeout:
        return True, "timeout_transitorio"
    except Exception as e:
        return True, f"errore_{str(e)[:60]}"


# ──────────────────────────────────────────────
# Job produzione
# ──────────────────────────────────────────────

def _run_check_job() -> dict:
    supabase = _get_supabase()
    now = _now()

    records = _fetch_batch(supabase)
    if not records:
        return {"status": "ok", "message": "Nessun annuncio da verificare", "checked": 0}

    subito_records = [r for r in records if "subito" in r.get("source", "").lower()]
    vinted_records = [r for r in records if "vinted" in r.get("source", "").lower()]

    sold_count    = 0
    checked_count = 0
    errors        = 0

    # ── Subito ──
    for rec in subito_records:
        url    = rec.get("url", "")
        rec_id = rec["id"]
        if not url:
            continue
        try:
            available, _ = _check_subito(url)
        except Exception as e:
            print(f"[checker] Subito error id={rec_id}: {e}")
            errors += 1
            supabase.table("scan_results").update({"last_checked_at": now}).eq("id", rec_id).execute()
            time.sleep(1)
            continue

        update = {"last_checked_at": now}
        if not available:
            update["is_sold"] = True
            update["sold_at"] = now
            sold_count += 1

        supabase.table("scan_results").update(update).eq("id", rec_id).execute()
        checked_count += 1
        time.sleep(1.5)

    # ── Vinted ──
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
            url    = rec.get("url", "")
            rec_id = rec["id"]
            if not url:
                continue
            try:
                available, _ = _check_vinted(session, url)
            except RuntimeError:
                try:
                    session   = _get_vinted_session()
                    available, _ = _check_vinted(session, url)
                except Exception as e2:
                    print(f"[checker] Vinted session refresh failed: {e2}")
                    errors += 1
                    continue
            except Exception as e:
                print(f"[checker] Vinted error id={rec_id}: {e}")
                errors += 1
                supabase.table("scan_results").update({"last_checked_at": now}).eq("id", rec_id).execute()
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


# ──────────────────────────────────────────────
# Job test / dry-run
# ──────────────────────────────────────────────

def _run_test_job(
    limit: int = 5,
    source_filter: str | None = None,
    dry_run: bool = True,
) -> dict:
    """
    Verifica `limit` annunci e ritorna il dettaglio record per record.
    Se dry_run=True non scrive nulla in DB — utile per validare la logica
    prima del lancio in produzione.
    """
    supabase = _get_supabase()
    now = _now()

    records = _fetch_batch(supabase, limit=limit, source_filter=source_filter)
    if not records:
        return {
            "status":   "ok",
            "dry_run":  dry_run,
            "message":  "Nessun annuncio trovato con i filtri indicati",
            "results":  [],
        }

    subito_records = [r for r in records if "subito" in r.get("source", "").lower()]
    vinted_records = [r for r in records if "vinted" in r.get("source", "").lower()]

    detail: list[dict] = []

    # ── Subito ──
    for rec in subito_records:
        url    = rec.get("url", "")
        rec_id = rec["id"]
        entry  = {
            "id":           rec_id,
            "source":       rec.get("source"),
            "keyword":      rec.get("keyword"),
            "title":        rec.get("title"),
            "price_value":  rec.get("price_value"),
            "score":        rec.get("score"),
            "url":          url,
            "available":    None,
            "reason":       None,
            "action":       "skip — url vuoto",
            "dry_run":      dry_run,
        }
        if not url:
            detail.append(entry)
            continue

        try:
            available, reason = _check_subito(url)
        except Exception as e:
            entry["reason"] = f"errore_{str(e)[:60]}"
            entry["action"] = "skip — errore"
            detail.append(entry)
            time.sleep(1)
            continue

        entry["available"] = available
        entry["reason"]    = reason

        if not dry_run:
            update = {"last_checked_at": now}
            if not available:
                update["is_sold"] = True
                update["sold_at"] = now
            supabase.table("scan_results").update(update).eq("id", rec_id).execute()
            entry["action"] = "scritto in DB"
        else:
            entry["action"] = "marcato sold [DRY RUN]" if not available else "nessuna modifica [DRY RUN]"

        detail.append(entry)
        time.sleep(1.5)

    # ── Vinted ──
    if vinted_records:
        try:
            session = _get_vinted_session()
        except Exception as e:
            for rec in vinted_records:
                detail.append({
                    "id":      rec["id"],
                    "source":  rec.get("source"),
                    "keyword": rec.get("keyword"),
                    "title":   rec.get("title"),
                    "url":     rec.get("url"),
                    "available": None,
                    "reason":  f"session_failed_{str(e)[:60]}",
                    "action":  "skip — sessione Vinted non ottenuta",
                    "dry_run": dry_run,
                })
            # Restituiamo quello che abbiamo
            return _build_test_response(detail, dry_run, source_filter)

        for rec in vinted_records:
            url    = rec.get("url", "")
            rec_id = rec["id"]
            entry  = {
                "id":          rec_id,
                "source":      rec.get("source"),
                "keyword":     rec.get("keyword"),
                "title":       rec.get("title"),
                "price_value": rec.get("price_value"),
                "score":       rec.get("score"),
                "url":         url,
                "available":   None,
                "reason":      None,
                "action":      "skip — url vuoto",
                "dry_run":     dry_run,
            }
            if not url:
                detail.append(entry)
                continue

            try:
                available, reason = _check_vinted(session, url)
            except RuntimeError:
                try:
                    session       = _get_vinted_session()
                    available, reason = _check_vinted(session, url)
                except Exception as e2:
                    entry["reason"] = f"session_refresh_failed_{str(e2)[:60]}"
                    entry["action"] = "skip — sessione non rinnovata"
                    detail.append(entry)
                    continue
            except Exception as e:
                entry["reason"] = f"errore_{str(e)[:60]}"
                entry["action"] = "skip — errore"
                detail.append(entry)
                time.sleep(0.5)
                continue

            entry["available"] = available
            entry["reason"]    = reason

            if not dry_run:
                update = {"last_checked_at": now}
                if not available:
                    update["is_sold"] = True
                    update["sold_at"] = now
                supabase.table("scan_results").update(update).eq("id", rec_id).execute()
                entry["action"] = "scritto in DB"
            else:
                entry["action"] = "marcato sold [DRY RUN]" if not available else "nessuna modifica [DRY RUN]"

            detail.append(entry)
            time.sleep(0.5)

    return _build_test_response(detail, dry_run, source_filter)


def _build_test_response(detail: list[dict], dry_run: bool, source_filter: str | None) -> dict:
    sold_preview  = [d for d in detail if d.get("available") is False]
    ok_preview    = [d for d in detail if d.get("available") is True]
    errors_detail = [d for d in detail if d.get("available") is None]
    return {
        "status":        "ok",
        "dry_run":       dry_run,
        "source_filter": source_filter or "all",
        "checked":       len(detail),
        "would_mark_sold": len(sold_preview),
        "available":     len(ok_preview),
        "errors":        len(errors_detail),
        "results":       detail,
    }


async def run_availability_test(
    limit: int = 5,
    source: str = "all",
    dry_run: bool = True,
) -> dict:
    """Entry point per l'endpoint /test/check-availability."""
    source_filter = None if source == "all" else source
    limit = max(1, min(limit, 20))  # cap 1–20
    try:
        return await asyncio.to_thread(
            _run_test_job, limit, source_filter, dry_run
        )
    except Exception as e:
        return {"status": "error", "detail": str(e)}
