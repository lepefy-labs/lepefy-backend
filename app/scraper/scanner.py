import os
import re
import json
import time
import asyncio
import requests
import httpx
from bs4 import BeautifulSoup
from supabase import create_client, Client

SCRAPERAPI_KEY    = os.getenv("SCRAPERAPI_KEY")
SCRAPERAPI_URL    = "http://api.scraperapi.com"

WEBSHARE_PROXY_USER = os.getenv("WEBSHARE_PROXY_USER")
WEBSHARE_PROXY_PASS = os.getenv("WEBSHARE_PROXY_PASS")
WEBSHARE_PROXY_HOST = "p.webshare.io"
WEBSHARE_PROXY_PORT = "80"

# Lista proxy Webshare con rotazione round-robin
WEBSHARE_PROXIES = [
    ("31.59.20.176",    "6754"),
    ("198.23.239.134",  "6540"),
    ("31.56.127.193",   "7684"),
    ("45.38.107.97",    "6014"),
    ("107.172.163.27",  "6543"),
    ("216.10.27.159",   "6837"),
    ("142.111.67.146",  "5611"),
    ("191.96.254.138",  "6185"),
    ("31.58.9.4",       "6077"),
    ("23.229.19.94",    "8689"),
]
_proxy_index = 0

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY")
EBAY_APP_ID          = os.getenv("EBAY_APP_ID")
EBAY_API_URL         = "https://svcs.ebay.com/services/search/FindingService/v1"
EBAY_FEE_RATE        = 0.12  # fee eBay ~12%, nettata dal prezzo venduto

# Modalita scraping: "scraperapi" | "webshare" | "direct"
SCRAPER_MODE = os.getenv("SCRAPER_MODE", "scraperapi").lower()

HAIKU_INPUT_COST_PER_M  = 0.80
HAIKU_OUTPUT_COST_PER_M = 4.00

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ──────────────────────────────────────────────
# Metodi di fetch
# ──────────────────────────────────────────────

def _fetch_via_scraperapi(url: str) -> requests.Response:
    params = {
        "api_key": SCRAPERAPI_KEY,
        "url": url,
        # country_code rimosso: non incluso nel piano attuale
    }
    for attempt in range(3):
        try:
            r = requests.get(SCRAPERAPI_URL, params=params, timeout=60)
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def _fetch_via_webshare(url: str) -> requests.Response:
    """Rotazione round-robin tra i proxy disponibili con fallback automatico."""
    global _proxy_index
    errors = []

    for attempt in range(len(WEBSHARE_PROXIES)):
        host, port = WEBSHARE_PROXIES[_proxy_index % len(WEBSHARE_PROXIES)]
        _proxy_index += 1

        proxy_url = f"http://{WEBSHARE_PROXY_USER}:{WEBSHARE_PROXY_PASS}@{host}:{port}"
        proxies = {"http": proxy_url, "https": proxy_url}

        try:
            r = requests.get(url, headers=HEADERS, proxies=proxies, timeout=30)
            r.raise_for_status()
            return r
        except Exception as e:
            errors.append(f"{host}:{port} -> {str(e)[:50]}")
            time.sleep(2)

    raise Exception(f"Tutti i proxy falliti: {errors}")


def _fetch_direct(url: str) -> requests.Response:
    """Chiamata diretta senza proxy — funziona solo in locale."""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def _fetch_html(url: str) -> requests.Response:
    """Router: seleziona il metodo in base a SCRAPER_MODE."""
    if SCRAPER_MODE == "webshare":
        return _fetch_via_webshare(url)
    elif SCRAPER_MODE == "direct":
        return _fetch_direct(url)
    else:
        return _fetch_via_scraperapi(url)


# ──────────────────────────────────────────────
# Estrazione dati
# ──────────────────────────────────────────────

def _extract_price_value(price_str: str) -> float | None:
    if not price_str or price_str == "N/D":
        return None
    cleaned = re.sub(r"[^\d,.]", "", price_str).replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_price(ad: dict) -> str:
    features = ad.get("features", {})
    if isinstance(features, dict):
        price_feature = features.get("/price", {})
        vals = price_feature.get("values", [])
        if vals and isinstance(vals[0], dict):
            return vals[0].get("value", vals[0].get("key", "N/D"))
    return "N/D"


def _extract_shipping(ad: dict) -> str:
    features = ad.get("features", {})
    if isinstance(features, dict):
        shipping_feature = features.get("/shipping")
        if shipping_feature:
            vals = shipping_feature.get("values", [])
            if vals and isinstance(vals[0], dict):
                return vals[0].get("value", "non specificata")
    return "non specificata"


def _extract_url(ad: dict) -> str:
    urls = ad.get("urls", {})
    if isinstance(urls, dict) and urls:
        return urls.get("default", next(iter(urls.values()), ""))
    urn = ad.get("urn", "")
    if urn:
        parts = urn.split(":")
        if len(parts) >= 5:
            return f"https://www.subito.it/annunci/{parts[-1]}.htm"
    return ""


def _extract_location(ad: dict) -> str:
    geo = ad.get("geo", {})
    if not isinstance(geo, dict):
        return ""
    city = geo.get("city", {})
    region = geo.get("region", {})
    city_val = city.get("value", "") if isinstance(city, dict) else str(city)
    region_val = region.get("value", "") if isinstance(region, dict) else str(region)
    return f"{city_val}, {region_val}".strip(", ")


# ──────────────────────────────────────────────
# eBay Completed Listings
# ──────────────────────────────────────────────

def _get_ebay_market_value(title: str) -> float | None:
    """
    Interroga eBay Finding API per gli articoli venduti (completed listings).
    Ritorna la mediana dei prezzi nettata delle fee eBay (~12%).
    Ritorna None se meno di 5 risultati — Claude userà la stima AI come fallback.
    """
    if not EBAY_APP_ID:
        return None

    # Usa i primi 5 token del titolo come query — abbastanza specifici, non troppo rigidi
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
        "itemFilter(1).name": "Condition",
        "itemFilter(1).value": "3000",  # Used
        "sortOrder": "EndTimeSoonest",
        "paginationInput.entriesPerPage": "20",
    }

    try:
        r = httpx.get(EBAY_API_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        resp = data.get("findCompletedItemsResponse", [{}])[0]
        items = resp.get("searchResult", [{}])[0].get("item", [])

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

        if len(prices) < 5:
            return None  # troppo pochi dati, fallback su stima AI

        # Mediana dei prezzi venduti
        prices.sort()
        median = prices[len(prices) // 2]

        # Netta le fee eBay (~12%) per ottenere il valore netto al venditore
        return round(median * (1 - EBAY_FEE_RATE), 2)

    except Exception:
        return None


# ──────────────────────────────────────────────
# AI Scoring
# ──────────────────────────────────────────────

def _score_ad(title: str, price: str, location: str, body: str, keyword: str, shipping: str, ebay_value: float | None = None) -> tuple[dict, dict]:
    """
    Chiama Claude Haiku per valutare l'annuncio.
    Retry automatico su errore 529 (API sovraccarica) — max 3 tentativi.
    Ritorna (risultato_ai, usage).
    """
    shipping_info = (
        f"Spedizione dichiarata dal venditore: {shipping}"
        if shipping and shipping != "non specificata"
        else "Spedizione non specificata dal venditore — stima €6"
    )

    ebay_anchor = (
        f"Valore di mercato REALE (mediana vendite concluse eBay Italia, netto fee 12%): €{ebay_value}
"
        f"Usa questo come riferimento principale per il valore_stimato. "
        f"Il margine_stimato deve essere calcolato rispetto a questo valore, non a prezzi di annunci attivi."
        if ebay_value
        else "Nessun dato eBay disponibile — stima il valore di mercato basandoti sulla tua conoscenza del settore."
    )

    prompt = f"""Sei un esperto di elettronica usata e flipping su marketplace italiani (Subito.it).

Keyword cercata: "{keyword}"

Annuncio:
Titolo: {title}
Prezzo richiesto: {price}
Città: {location}
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
  valgono poco usati (€5-20 cadauno), NON gonfiare il totale per la loro presenza

Regole importanti:
- Il valore totale di un kit e SEMPRE inferiore alla somma dei singoli pezzi
- Sii conservativo: meglio sottostimare che sovrastimare
- Il valore di rivendita non puo superare del 40% il prezzo richiesto per articoli comuni

Calcolo margine lordo:
- margine_stimato = valore_rivendita_stimato - prezzo_acquisto - costo_spedizione
- Usa la spedizione dichiarata. Se non specificata, usa €6.
- NON applicare commissioni di piattaforma.
- Se il margine lordo e sotto €15, metti score massimo 4.

Rispondi SOLO con JSON valido, nessun testo extra:
{{"score":7,"verdict":"AFFARE","valore_stimato":320,"margine_stimato":70,"motivazione":"max 15 parole","rischi":"max 10 parole"}}

Valori verdict: AFFARE (margine>40€), OK (margine 15-40€), EVITA (margine<15€ o non pertinente)"""

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
                # API sovraccarica — attendi e riprova
                time.sleep(10 * (attempt + 1))  # 10s poi 20s
                continue
            # Altri errori HTTP o terzo tentativo fallito
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
# Fetch Subito
# ──────────────────────────────────────────────

def _fetch_subito(keyword: str, max_results: int = 30) -> list[dict]:
    search_url = (
        f"https://www.subito.it/annunci-italia/vendita/usato/"
        f"?q={keyword.replace(' ', '+')}&sort=date_desc"
    )
    response = _fetch_html(search_url)
    soup = BeautifulSoup(response.text, "html.parser")
    next_data_tag = soup.find("script", id="__NEXT_DATA__")
    if not next_data_tag:
        raise ValueError("__NEXT_DATA__ non trovato")

    next_data = json.loads(next_data_tag.string)
    items_data = (
        next_data
        .get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
        .get("items", {})
    )
    ads_raw = items_data.get("originalList", []) if isinstance(items_data, dict) else []

    results = []
    for ad in ads_raw[:max_results]:
        if not isinstance(ad, dict):
            continue
        price_raw = _extract_price(ad)
        results.append({
            "title": ad.get("subject", "N/D"),
            "price": price_raw,
            "price_value": _extract_price_value(price_raw),
            "location": _extract_location(ad),
            "date": ad.get("date", ""),
            "url": _extract_url(ad),
            "body": ad.get("body", ""),
            "shipping": _extract_shipping(ad),
            "source": "Subito.it",
        })
    return results


# ──────────────────────────────────────────────
# Scan & Save
# ──────────────────────────────────────────────

def _get_active_keywords() -> list[str]:
    supabase = _get_supabase()
    response = (
        supabase.table("keywords")
        .select("keyword")
        .eq("active", True)
        .execute()
    )
    return [row["keyword"].lower() for row in (response.data or [])]


def _scan_keyword(keyword: str) -> dict:
    """
    Scansiona una keyword e salva nel pool condiviso scan_results.
    - Claude chiamato SOLO per annunci nuovi o con prezzo cambiato
    - Retry su errore 529 (max 3 tentativi, backoff 10s/20s)
    - Annunci con scoring fallito (N/D) vengono scartati
    - Annunci non pertinenti o con margine <= 0 vengono scartati
    - Annunci con prezzo calato >= 15% vengono rivalutati e rinotificati
    """
    supabase = _get_supabase()
    items = _fetch_subito(keyword, max_results=30)

    if not items:
        return {"keyword": keyword, "found": 0, "new": 0, "updated": 0,
                "skipped": 0, "rejected": 0,
                "tokens": {"input": 0, "output": 0, "cost_usd": 0.0}}

    # Recupera URL e prezzi già presenti in DB per questa keyword
    existing_response = (
        supabase.table("scan_results")
        .select("url, price_value")
        .eq("keyword", keyword)
        .execute()
    )
    existing = {row["url"]: row["price_value"] for row in (existing_response.data or [])}

    total_input = 0
    total_output = 0
    new_rows = []
    skipped = 0
    rejected = 0
    updated = 0

    for item in items:
        if not item.get("price_value"):
            continue

        url = item["url"]
        price_value = item["price_value"]

        if url in existing:
            old_price = existing[url]
            if old_price == price_value:
                skipped += 1
                continue
            # Rinotifica solo se il prezzo è calato di almeno il 15%
            if old_price and (old_price - price_value) / old_price >= 0.15:
                price_changed = True
            else:
                # Prezzo cambiato ma calo < 15% — aggiorna DB senza rinotificare
                supabase.table("scan_results").update({
                    "price_raw": item["price"],
                    "price_value": price_value,
                }).eq("url", url).execute()
                skipped += 1
                continue
        else:
            price_changed = False

        # Pre-filtro: keyword deve apparire nel titolo o nei primi 200 char del body
        if (keyword not in item["title"].lower() and
                keyword not in item.get("body", "")[:200].lower()):
            rejected += 1
            continue

        # Recupera valore di mercato reale da eBay Completed Listings
        ebay_value = _get_ebay_market_value(item["title"])

        # Se il prezzo Subito è >= valore eBay, il margine è certamente negativo
        # Scarta immediatamente senza chiamare Claude — risparmio token
        if ebay_value and ebay_value <= item["price_value"]:
            rejected += 1
            continue

        ai, usage = _score_ad(
            item["title"], item["price"], item["location"],
            item.get("body", ""), keyword, item.get("shipping", "non specificata"),
            ebay_value=ebay_value
        )
        total_input  += usage.get("input_tokens", 0)
        total_output += usage.get("output_tokens", 0)

        # Scarta annunci con scoring fallito (errore 529 o altro)
        if ai.get("verdict") == "N/D" or ai.get("score") is None:
            rejected += 1
            continue

        margine = ai.get("margine_stimato")

        # Scarta annunci non pertinenti o con margine sotto soglia minima
        if ai.get("verdict") == "EVITA" and ai.get("score") == 1:
            rejected += 1
            continue
        if margine is None or margine < 15:
            rejected += 1
            continue

        if price_changed:
            supabase.table("scan_results").update({
                "price_raw": item["price"],
                "price_value": price_value,
                "score": ai.get("score"),
                "margine_stimato": margine,
                "motivazione": ai.get("motivazione"),
                "rischi": ai.get("rischi"),
                "ebay_valore_mercato": ebay_value,
            }).eq("url", url).execute()

            # Resetta notifications_log per rinotificare con il nuovo prezzo
            scan_ids = (
                supabase.table("scan_results")
                .select("id")
                .eq("url", url)
                .execute()
            )
            if scan_ids.data:
                scan_id = scan_ids.data[0]["id"]
                supabase.table("notifications_log").delete().eq("scan_result_id", scan_id).execute()
            updated += 1
        else:
            new_rows.append({
                "keyword": keyword,
                "title": item["title"],
                "price_raw": item["price"],
                "price_value": price_value,
                "location": item["location"],
                "url": url,
                "date_listed": item["date"] or None,
                "source": item["source"],
                "score": ai.get("score"),
                "margine_stimato": margine,
                "motivazione": ai.get("motivazione"),
                "rischi": ai.get("rischi"),
                "ebay_valore_mercato": ebay_value,
            })

    if new_rows:
        supabase.table("scan_results").upsert(
            new_rows, on_conflict="url", ignore_duplicates=True
        ).execute()

    cost_usd = (
        (total_input  / 1_000_000 * HAIKU_INPUT_COST_PER_M) +
        (total_output / 1_000_000 * HAIKU_OUTPUT_COST_PER_M)
    )

    if total_input > 0:
        supabase.table("ai_usage_log").insert({
            "keyword": keyword,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cost_usd": round(cost_usd, 5),
            "deals_scored": len(new_rows) + updated + rejected,
        }).execute()

    return {
        "keyword": keyword,
        "found": len(items),
        "new": len(new_rows),
        "updated": updated,
        "skipped": skipped,
        "rejected": rejected,
        "scraper_mode": SCRAPER_MODE,
        "tokens": {
            "input": total_input,
            "output": total_output,
            "cost_usd": round(cost_usd, 5),
        },
    }


# ──────────────────────────────────────────────
# Entry points async
# ──────────────────────────────────────────────

async def run_lepe_scan(keyword: str, max_results: int = 15) -> list[dict]:
    """Endpoint /test-scan: ritorna annunci senza filtrare né salvare."""
    try:
        return await asyncio.to_thread(_fetch_subito, keyword, max_results)
    except requests.exceptions.HTTPError as e:
        return [{"title": "Errore HTTP", "price": str(e), "source": "Subito.it"}]
    except requests.exceptions.Timeout:
        return [{"title": "Timeout", "price": "Nessuna risposta in tempo", "source": "Subito.it"}]
    except ValueError as e:
        return [{"title": "Parsing fallito", "price": str(e), "source": "Subito.it"}]
    except Exception as e:
        return [{"title": "Errore Tecnico", "price": str(e), "source": "Subito.it"}]


async def run_scan_and_save() -> dict:
    """
    Cron job: legge le keyword attive da Supabase, scansiona ciascuna
    una volta sola con 10 secondi di pausa tra l'una e l'altra.
    """
    try:
        keywords = await asyncio.to_thread(_get_active_keywords)
        if not keywords:
            return {"status": "ok", "message": "Nessuna keyword attiva"}

        results = []
        for i, keyword in enumerate(keywords):
            if i > 0:
                await asyncio.sleep(10)
            result = await asyncio.to_thread(_scan_keyword, keyword)
            results.append(result)

        total_cost = sum(r["tokens"]["cost_usd"] for r in results)
        return {
            "status": "ok",
            "scraper_mode": SCRAPER_MODE,
            "keywords_scanned": len(keywords),
            "results": results,
            "total_cost_usd": round(total_cost, 5),
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}
