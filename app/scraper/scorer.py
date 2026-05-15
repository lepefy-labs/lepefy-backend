"""
scorer.py — Scoring AI + eBay per annunci grezzi non ancora valutati.

Responsabilità:
- Legge annunci con scored=false da scan_results
- Per ogni annuncio: chiama eBay Completed Listings per valore mercato reale
- Se prezzo Subito >= valore eBay → scarta (margine certamente negativo)
- Chiama Claude Haiku con ancora eBay per scoring preciso
- Scarta annunci non pertinenti o con margine < 15€
- Aggiorna scan_results con score, margine, motivazione, rischi, ebay_valore_mercato
- Setta scored=true dopo valutazione (anche se scartato)
- Log token su ai_usage_log
"""

import os
import json
import time
import asyncio
import httpx
from supabase import create_client, Client

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY")
EBAY_APP_ID          = os.getenv("EBAY_APP_ID")
EBAY_API_URL         = "https://svcs.ebay.com/services/search/FindingService/v1"
EBAY_FEE_RATE        = 0.12

HAIKU_INPUT_COST_PER_M  = 0.80
HAIKU_OUTPUT_COST_PER_M = 4.00

# Numero massimo di annunci da scorare per esecuzione
SCORE_BATCH_SIZE = int(os.getenv("SCORE_BATCH_SIZE", "50"))


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ──────────────────────────────────────────────
# eBay Completed Listings
# ──────────────────────────────────────────────

def _get_ebay_market_value(title: str) -> float | None:
    """
    Interroga eBay Finding API per gli articoli venduti.
    Ritorna la mediana dei prezzi nettata delle fee eBay (~12%).
    Ritorna None se meno di 5 risultati.
    """
    if not EBAY_APP_ID:
        return None

    search_terms = " ".join(title.split()[:5])

    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.0.0",
        "SECURITY-APPNAME": EBAY_APP_ID,
        "RESPONSE-DATA-FORMAT": "JSON",
        "keywords": search_terms,
        "GLOBAL-ID": "EBAY-IT",
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        "sortOrder": "EndTimeSoonest",
        "paginationInput.entriesPerPage": "20",
    }

    try:
        r = httpx.get(EBAY_API_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        resp = data.get("findCompletedItemsResponse", [{}])[0]
        ack = resp.get("ack", ["N/D"])[0]
        total_found = resp.get("paginationOutput", [{}])[0].get("totalEntries", ["0"])[0]
        items = resp.get("searchResult", [{}])[0].get("item", [])

        # Debug info sempre disponibile
        debug_info = {
            "search_terms": search_terms,
            "ack": ack,
            "total_found": total_found,
            "items_returned": len(items),
        }

        prices = []
        for item in items:
            try:
                price = float(
                    item.get("sellingStatus", [{}])[0]
                    .get("currentPrice", [{}])[0]
                    .get("__value__", 0)
                )
                if price > 0:
                    prices.append(price)
            except (IndexError, KeyError, ValueError):
                continue

        debug_info["prices_extracted"] = prices
        debug_info["prices_count"] = len(prices)

        if len(prices) < 5:
            debug_info["result"] = f"None — meno di 5 prezzi ({len(prices)} trovati)"
            return None, debug_info

        prices.sort()
        median = prices[len(prices) // 2]
        result = round(median * (1 - EBAY_FEE_RATE), 2)
        debug_info["median_gross"] = median
        debug_info["result"] = result
        return result, debug_info

    except Exception as e:
        return None, {"error": str(e), "search_terms": search_terms}


# ──────────────────────────────────────────────
# AI Scoring
# ──────────────────────────────────────────────

def _extract_price_value(price_str: str) -> float | None:
    import re
    if not price_str or price_str == "N/D":
        return None
    cleaned = re.sub(r"[^\d,.]", "", price_str).replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _score_ad(
    title: str,
    price: str,
    location: str,
    body: str,
    keyword: str,
    shipping: str,
    condition: str = "non specificata",
    ebay_value: float | None = None,
) -> tuple[dict, dict]:
    """
    Chiama Claude Haiku per valutare l'annuncio.
    Usa il valore eBay come ancora se disponibile.
    Retry automatico su errore 529 — max 3 tentativi.
    """
    shipping_info = (
        f"Spedizione dichiarata dal venditore: {shipping}"
        if shipping and shipping != "non specificata"
        else "Spedizione non specificata dal venditore — stima €6"
    )

    if ebay_value:
        ebay_anchor = (
            f"Valore di mercato REALE (mediana vendite concluse eBay Italia, netto fee 12%): €{ebay_value}. "
            f"Usa questo come riferimento principale per il valore_stimato. "
            f"Il margine_stimato deve essere calcolato rispetto a questo valore, non a prezzi di annunci attivi."
        )
    else:
        ebay_anchor = "Nessun dato eBay disponibile — stima il valore di mercato basandoti sulla tua conoscenza del settore."

    prompt = f"""Sei un esperto di elettronica usata e flipping su marketplace italiani (Subito.it).

Keyword cercata: "{keyword}"

Annuncio:
Titolo: {title}
Prezzo richiesto: {price}
Città: {location}
Condizione dichiarata: {condition}
{shipping_info}
{ebay_anchor}
Descrizione: {body[:800] if body else 'N/D'}

REGOLA FONDAMENTALE: se l'articolo principale NON corrisponde alla keyword cercata
(citato solo come accessorio compatibile o riferimento secondario),
assegna score 1 e verdict EVITA con motivazione "Annuncio non pertinente".

Se pertinente, analizza ogni componente separatamente:
- Corpo macchina: stima valore usato reale sul mercato italiano
- Ogni obiettivo: stima valore usato reale separatamente
- Accessori minori (borse, filtri, batterie extra, schede SD, tracolla):
  valgono poco usati (5-20 euro cadauno), NON gonfiare il totale per la loro presenza

Regole importanti:
- Il valore totale di un kit e SEMPRE inferiore alla somma dei singoli pezzi
- Sii conservativo: meglio sottostimare che sovrastimare
- Il valore di rivendita non puo superare del 40% il prezzo richiesto per articoli comuni

Calcolo margine lordo:
- margine_stimato = valore_rivendita_stimato - prezzo_acquisto - costo_spedizione
- Usa la spedizione dichiarata. Se non specificata, usa 6 euro.
- NON applicare commissioni di piattaforma.
- Se il margine lordo e sotto 15 euro, metti score massimo 4.

Rispondi SOLO con JSON valido, nessun testo extra:
{{"score":7,"verdict":"AFFARE","valore_stimato":320,"margine_stimato":70,"motivazione":"max 15 parole","rischi":"max 10 parole"}}

Valori verdict: AFFARE (margine>40), OK (margine 15-40), EVITA (margine<15 o non pertinente)"""

    for attempt in range(3):
        try:
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 200,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            usage = data.get("usage", {})
            text = data["content"][0]["text"].replace("```json", "").replace("```", "").strip()
            result = json.loads(text)

            # Sanity check: margine non può superare 1.5x il prezzo di acquisto
            price_num = _extract_price_value(price) or 0
            if price_num > 0 and result.get("margine_stimato"):
                max_margine = price_num * 1.5
                if result["margine_stimato"] > max_margine:
                    result["margine_stimato"] = round(max_margine * 0.5)
                    result["score"] = min(result.get("score", 5), 5)
                    result["verdict"] = "OK"

            return result, usage

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 529 and attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            return {"score": None, "verdict": "N/D", "valore_stimato": None,
                    "margine_stimato": None, "motivazione": f"Errore API: {str(e)[:50]}",
                    "rischi": ""}, {}
        except Exception as e:
            return {"score": None, "verdict": "N/D", "valore_stimato": None,
                    "margine_stimato": None, "motivazione": f"Scoring non disponibile: {str(e)[:50]}",
                    "rischi": ""}, {}

    return {"score": None, "verdict": "N/D", "valore_stimato": None,
            "margine_stimato": None, "motivazione": "Scoring non disponibile dopo 3 tentativi",
            "rischi": ""}, {}


# ──────────────────────────────────────────────
# Score job
# ──────────────────────────────────────────────

def _run_score_job() -> dict:
    """
    Legge annunci con scored=false, li valuta con eBay + Claude
    e aggiorna scan_results.
    """
    supabase = _get_supabase()

    # Legge batch di annunci non ancora scorati
    response = (
        supabase.table("scan_results")
        .select("*")
        .eq("scored", False)
        .order("created_at", desc=False)  # prima i più vecchi
        .limit(SCORE_BATCH_SIZE)
        .execute()
    )
    ads = response.data or []

    if not ads:
        return {"status": "ok", "message": "Nessun annuncio da scorare"}

    total_input = 0
    total_output = 0
    scored = 0
    rejected = 0
    errors = 0

    # Raggruppa per keyword per il log
    keyword_costs: dict[str, dict] = {}

    for ad in ads:
        keyword = ad.get("keyword", "")
        title = ad.get("title", "")
        price = ad.get("price_raw", "N/D")
        price_value = ad.get("price_value") or 0
        location = ad.get("location", "")
        body = ad.get("body", "")
        shipping = ad.get("shipping", "non specificata")
        ad_id = ad["id"]

        # 1. Recupera valore eBay
        ebay_result = _get_ebay_market_value(title)
        ebay_value, ebay_debug = ebay_result if isinstance(ebay_result, tuple) else (ebay_result, {})

        # 2. Se prezzo Subito >= valore eBay → scarta senza chiamare Claude
        if ebay_value and isinstance(ebay_value, (int, float)) and ebay_value <= price_value:
            supabase.table("scan_results").update({
                "scored": True,
                "score": 1,
                "verdict": "EVITA",
                "margine_stimato": None,
                "motivazione": "Prezzo superiore al valore di mercato eBay",
                "rischi": "",
                "ebay_valore_mercato": ebay_value,
            }).eq("id", ad_id).execute()
            rejected += 1
            continue

        # 3. Scoring AI
        condition = ad.get("condition", "non specificata")
        ai, usage = _score_ad(title, price, location, body, keyword, shipping, condition, ebay_value)

        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_input += input_tokens
        total_output += output_tokens

        if keyword not in keyword_costs:
            keyword_costs[keyword] = {"input": 0, "output": 0, "count": 0}
        keyword_costs[keyword]["input"] += input_tokens
        keyword_costs[keyword]["output"] += output_tokens
        keyword_costs[keyword]["count"] += 1

        # 4. Scoring fallito (errore API)
        if ai.get("verdict") == "N/D" or ai.get("score") is None:
            # Non marcare come scored — verrà riprovato al prossimo ciclo
            errors += 1
            continue

        margine = ai.get("margine_stimato")

        # 5. Scarta non pertinenti o margine < 15
        if (ai.get("verdict") == "EVITA" and ai.get("score") == 1) or \
           (margine is None or margine < 15):
            supabase.table("scan_results").update({
                "scored": True,
                "score": ai.get("score"),
                "margine_stimato": margine,
                "motivazione": ai.get("motivazione"),
                "rischi": ai.get("rischi"),
                "ebay_valore_mercato": ebay_value,
            }).eq("id", ad_id).execute()
            rejected += 1
            continue

        # 6. Salva score
        supabase.table("scan_results").update({
            "scored": True,
            "score": ai.get("score"),
            "margine_stimato": margine,
            "motivazione": ai.get("motivazione"),
            "rischi": ai.get("rischi"),
            "ebay_valore_mercato": ebay_value,
        }).eq("id", ad_id).execute()
        scored += 1

    # Log costi per keyword
    cost_usd_total = (
        (total_input  / 1_000_000 * HAIKU_INPUT_COST_PER_M) +
        (total_output / 1_000_000 * HAIKU_OUTPUT_COST_PER_M)
    )

    if total_input > 0:
        for kw, costs in keyword_costs.items():
            kw_cost = (
                (costs["input"]  / 1_000_000 * HAIKU_INPUT_COST_PER_M) +
                (costs["output"] / 1_000_000 * HAIKU_OUTPUT_COST_PER_M)
            )
            supabase.table("ai_usage_log").insert({
                "keyword": kw,
                "input_tokens": costs["input"],
                "output_tokens": costs["output"],
                "cost_usd": round(kw_cost, 5),
                "deals_scored": costs["count"],
            }).execute()

    return {
        "status": "ok",
        "processed": len(ads),
        "scored": scored,
        "rejected": rejected,
        "errors": errors,
        "total_cost_usd": round(cost_usd_total, 5),
    }


async def run_score_job() -> dict:
    return await asyncio.to_thread(_run_score_job)
