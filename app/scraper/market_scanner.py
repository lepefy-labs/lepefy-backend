"""
market_scanner.py — Lepefy
Scanner di mercato separato dallo scanner operativo.

Scopo: raccogliere dati statistici grezzi sul mercato dell'usato italiano
       per costruire distribuzioni di prezzo, stimare time-to-sell e
       alimentare la tabella market_snapshots.

Differenze dallo scanner operativo:
- Nessuna chiamata AI (zero costo Anthropic)
- Nessun filtro di margine — salva tutto ciò che è pertinente
- Keyword fisse definite da Lepefy (tassonomia controllata)
- Volumi più alti (fino a 100 annunci per keyword)
- Cron separato, frequenza più bassa (1-2x al giorno)
- Filtri leggeri lato Python per eliminare accessori non pertinenti
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

SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")
SCRAPERAPI_URL = "http://api.scraperapi.com"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")


# ---------------------------------------------------------------------------
# Tassonomia di mercato — keyword fisse controllate da Lepefy
# Struttura: categoria → lista di keyword da scansionare
# Aggiungere qui nuove categorie senza toccare il resto del codice
# ---------------------------------------------------------------------------
MARKET_TAXONOMY: dict[str, list[str]] = {
    "fotografia": [
        "sony a7",
        "sony a7 iii",
        "sony a7 iv",
        "sony a7r",
        "sony a6000",
        "sony a6400",
        "fujifilm x-t",
        "fujifilm x100",
        "canon eos r",
        "canon eos r6",
        "nikon z6",
        "nikon z7",
        "olympus om-d",
        "panasonic lumix g",
    ],
    "audio_hifi": [
        "amplificatore valvolare",
        "giradischi",
        "casse acustiche",
        "yamaha hs8",
        "focal alpha",
        "sennheiser hd",
        "audio technica at",
    ],
    "strumenti_musicali": [
        "chitarra fender stratocaster",
        "chitarra gibson les paul",
        "sintetizzatore moog",
        "roland juno",
        "korg minilogue",
        "pianoforte digitale",
    ],
    "elettronica": [
        "macbook pro m1",
        "macbook pro m2",
        "macbook air m2",
        "ipad pro",
        "iphone 14",
        "iphone 15",
    ],
    "videogiochi": [
        "ps5",
        "nintendo switch oled",
        "xbox series x",
        "steam deck",
    ],
}


# ---------------------------------------------------------------------------
# Blacklist — parole nel TITOLO che indicano un accessorio non pertinente
# Se una di queste è presente, l'annuncio viene scartato
# ---------------------------------------------------------------------------
ACCESSORY_BLACKLIST: set[str] = {
    "custodia", "cover", "pellicola", "vetro temperato",
    "caricabatterie", "caricatore", "batteria", "battery",
    "cavo", "adattatore", "filtro", "parasole",
    "tracolla", "cinghia", "strap",
    "borsa", "zaino", "borsina",
    "obiettivo compatibile", "grip", "telecomando",
    "manuale", "scatola vuota",
    "scheda sd", "memory card",
    "flash", "trigger", "pannello",
    "stand", "supporto",
}

# ---------------------------------------------------------------------------
# Mappa keyword → (marca, modello) — deterministico, zero AI
# La tassonomia è controllata, quindi possiamo hardcodare questi valori.
# ---------------------------------------------------------------------------
KEYWORD_BRAND_MODEL: dict[str, tuple[str, str]] = {
    # fotografia
    "sony a7":                    ("Sony",      "A7"),
    "sony a7 iii":                ("Sony",      "A7 III"),
    "sony a7 iv":                 ("Sony",      "A7 IV"),
    "sony a7r":                   ("Sony",      "A7R"),
    "sony a6000":                 ("Sony",      "A6000"),
    "sony a6400":                 ("Sony",      "A6400"),
    "fujifilm x-t":               ("Fujifilm",  "X-T"),
    "fujifilm x100":              ("Fujifilm",  "X100"),
    "canon eos r":                ("Canon",     "EOS R"),
    "canon eos r6":               ("Canon",     "EOS R6"),
    "nikon z6":                   ("Nikon",     "Z6"),
    "nikon z7":                   ("Nikon",     "Z7"),
    "olympus om-d":               ("Olympus",   "OM-D"),
    "panasonic lumix g":          ("Panasonic", "Lumix G"),
    # audio_hifi
    "amplificatore valvolare":    ("",          "Amplificatore valvolare"),
    "giradischi":                 ("",          "Giradischi"),
    "casse acustiche":            ("",          "Casse acustiche"),
    "yamaha hs8":                 ("Yamaha",    "HS8"),
    "focal alpha":                ("Focal",     "Alpha"),
    "sennheiser hd":              ("Sennheiser","HD"),
    "audio technica at":          ("Audio-Technica", "AT"),
    # strumenti_musicali
    "chitarra fender stratocaster": ("Fender",  "Stratocaster"),
    "chitarra gibson les paul":   ("Gibson",    "Les Paul"),
    "sintetizzatore moog":        ("Moog",      ""),
    "roland juno":                ("Roland",    "Juno"),
    "korg minilogue":             ("Korg",      "Minilogue"),
    "pianoforte digitale":        ("",          "Pianoforte digitale"),
    # elettronica
    "macbook pro m1":             ("Apple",     "MacBook Pro M1"),
    "macbook pro m2":             ("Apple",     "MacBook Pro M2"),
    "macbook air m2":             ("Apple",     "MacBook Air M2"),
    "ipad pro":                   ("Apple",     "iPad Pro"),
    "iphone 14":                  ("Apple",     "iPhone 14"),
    "iphone 15":                  ("Apple",     "iPhone 15"),
    # videogiochi
    "ps5":                        ("Sony",      "PS5"),
    "nintendo switch oled":       ("Nintendo",  "Switch OLED"),
    "xbox series x":              ("Microsoft", "Xbox Series X"),
    "steam deck":                 ("Valve",     "Steam Deck"),
}


# ---------------------------------------------------------------------------
# Price ranges per categoria — annunci fuori range vengono scartati
# Formato: categoria → (min_€, max_€)
# Soglie conservative per eliminare ovvi accessori/outlier
# ---------------------------------------------------------------------------
PRICE_RANGES: dict[str, tuple[float, float]] = {
    "fotografia":          (80.0,  8_000.0),
    "audio_hifi":          (30.0,  5_000.0),
    "strumenti_musicali":  (50.0,  8_000.0),
    "elettronica":         (100.0, 5_000.0),
    "videogiochi":         (15.0,  1_000.0),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_condition(ad: dict) -> str | None:
    """Estrae la condizione dell'articolo dalle features di Subito."""
    features = ad.get("features", {})
    if isinstance(features, dict):
        condition = features.get("/item_condition")
        if condition:
            vals = condition.get("values", [])
            if vals and isinstance(vals[0], dict):
                return vals[0].get("value")
    return None


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


# ---------------------------------------------------------------------------
# Filtri di pertinenza (nessuna AI)
# ---------------------------------------------------------------------------

def _is_relevant(title: str, keyword: str, price_value: float | None, categoria: str) -> tuple[bool, str]:
    """
    Ritorna (pertinente: bool, motivo_scarto: str).
    Applica tre filtri in cascata:
      1. Keyword presente nel titolo
      2. Nessuna parola della blacklist nel titolo
      3. Prezzo nel range atteso per la categoria
    """
    title_lower = title.lower()
    keyword_lower = keyword.lower()

    # Filtro 1 — keyword nel titolo
    keyword_tokens = keyword_lower.split()
    keyword_in_title = all(token in title_lower for token in keyword_tokens)

    # Filtro 2 — blacklist accessori
    # Applicata SOLO se la keyword non è nel titolo:
    # se la keyword c'è, il prodotto principale è quello cercato
    # e parole come "caricabatterie" descrivono accessori inclusi, non il prodotto.
    if not keyword_in_title:
        for word in ACCESSORY_BLACKLIST:
            if word in title_lower:
                return False, f"accessorio_blacklist:{word}"
        # Keyword non nel titolo e nessuna blacklist: scartiamo comunque
        return False, "keyword_non_nel_titolo"

    # Filtro 3 — price range
    if price_value is not None and categoria in PRICE_RANGES:
        min_p, max_p = PRICE_RANGES[categoria]
        if price_value < min_p:
            return False, f"prezzo_troppo_basso:{price_value}"
        if price_value > max_p:
            return False, f"prezzo_troppo_alto:{price_value}"

    return True, ""


# ---------------------------------------------------------------------------
# Fetch da Subito
# ---------------------------------------------------------------------------

def _fetch_subito_market(keyword: str, max_results: int = 100) -> list[dict]:
    """
    Fetch con volumi più alti rispetto allo scanner operativo.
    Gestisce la paginazione fino a max_results.
    """
    results = []
    page = 1
    per_page = 30

    while len(results) < max_results:
        # Prima pagina senza offset (identico a scanner.py che funziona).
        # Dalla seconda in poi usa &o= per la paginazione.
        offset = (page - 1) * per_page
        base_url = (
            f"https://www.subito.it/annunci-italia/vendita/usato/"
            f"?q={keyword.replace(' ', '+')}&sort=date_desc"
        )
        search_url = base_url if page == 1 else f"{base_url}&o={offset}"
        params = {
            "api_key": SCRAPERAPI_KEY,
            "url":     search_url,
        }

        try:
            response = requests.get(SCRAPERAPI_URL, params=params, timeout=60)
            response.raise_for_status()
        except Exception as e:
            print(f"[market_scanner] fetch error keyword={keyword} page={page}: {e}")
            break

        soup = BeautifulSoup(response.text, "html.parser")
        next_data_tag = soup.find("script", id="__NEXT_DATA__")
        if not next_data_tag:
            print(f"[market_scanner] __NEXT_DATA__ non trovato keyword={keyword} page={page} http={response.status_code} html_len={len(response.text)}")
            break

        try:
            next_data = json.loads(next_data_tag.string)
        except json.JSONDecodeError as e:
            print(f"[market_scanner] JSON parse error keyword={keyword} page={page}: {e}")
            break

        items_data = (
            next_data
            .get("props", {})
            .get("pageProps", {})
            .get("initialState", {})
            .get("items", {})
        )
        ads_raw = items_data.get("originalList", []) if isinstance(items_data, dict) else []

        if not ads_raw:
            break  # nessun altro risultato

        for ad in ads_raw:
            if not isinstance(ad, dict):
                continue
            price_raw = _extract_price(ad)
            results.append({
                "title":       ad.get("subject", "N/D"),
                "price":       price_raw,
                "price_value": _extract_price_value(price_raw),
                "location":    _extract_location(ad),
                "condizione":  _extract_condition(ad),
                "url":         _extract_url(ad),
                "source":      "Subito.it",
            })

        page += 1
        if len(ads_raw) < per_page:
            break  # ultima pagina

        time.sleep(2)  # rispetta rate limit

    return results[:max_results]


# ---------------------------------------------------------------------------
# Core: scansiona una keyword e salva in market_snapshots
# ---------------------------------------------------------------------------

def _market_scan_keyword(keyword: str, categoria: str) -> dict:
    supabase = _get_supabase()
    now = _now()

    items = _fetch_subito_market(keyword, max_results=100)

    if not items:
        return {
            "keyword": keyword, "categoria": categoria,
            "found": 0, "saved": 0, "skipped": 0, "sold": 0,
        }

    # URL già presenti in market_snapshots per questa keyword
    existing_response = (
        supabase.table("market_snapshots")
        .select("_url_ref, price_value")
        .eq("keyword", keyword)
        .eq("is_sold", False)
        .execute()
    )
    existing = {row["_url_ref"]: row["price_value"] for row in (existing_response.data or [])}

    seen_urls: set[str] = set()
    new_rows = []
    touch_urls = []
    skipped = 0

    for item in items:
        if not item.get("price_value") or not item.get("url"):
            continue

        url = item["url"]
        price_value = item["price_value"]
        seen_urls.add(url)

        relevant, _ = _is_relevant(item["title"], keyword, price_value, categoria)
        if not relevant:
            skipped += 1
            continue

        if url in existing:
            if existing[url] == price_value:
                touch_urls.append(url)
            else:
                # Prezzo cambiato — aggiorna
                supabase.table("market_snapshots").update({
                    "price_value":  price_value,
                    "last_seen_at": now,
                }).eq("_url_ref", url).eq("is_sold", False).execute()
        else:
            marca, modello = KEYWORD_BRAND_MODEL.get(keyword.lower(), ("", ""))
            new_rows.append({
                "keyword":       keyword,
                "categoria":     categoria,
                "marca":         marca or None,
                "modello":       modello or None,
                "source":        item["source"],
                "price_value":   price_value,
                "location":      item["location"],
                "condizione":    item.get("condizione"),
                "first_seen_at": now,
                "last_seen_at":  now,
                "_url_ref":      url,
                # score/margine/verdict = None — nessuna AI
            })

    # Batch insert nuovi
    if new_rows:
        supabase.table("market_snapshots").upsert(
            new_rows, on_conflict="_url_ref", ignore_duplicates=True
        ).execute()

    # Touch degli invariati
    for url in touch_urls:
        supabase.table("market_snapshots").update({
            "last_seen_at": now,
        }).eq("_url_ref", url).eq("is_sold", False).execute()

    # Marca venduti gli scomparsi
    sold = 0
    for url, _ in existing.items():
        if url not in seen_urls:
            supabase.table("market_snapshots").update({
                "is_sold": True,
                "sold_at": now,
            }).eq("_url_ref", url).eq("is_sold", False).execute()
            sold += 1

    return {
        "keyword":   keyword,
        "categoria": categoria,
        "found":     len(items),
        "saved":     len(new_rows),
        "skipped":   skipped,
        "sold":      sold,
    }


# ---------------------------------------------------------------------------
# Entry point — cron job
# ---------------------------------------------------------------------------

async def run_market_scan() -> dict:
    """
    Cron job separato da run_scan_and_save().
    Scansiona tutte le keyword della tassonomia fissa.
    Suggerita frequenza: 1-2 volte al giorno.
    """
    results = []
    total_saved = 0
    total_sold = 0
    i = 0

    for categoria, keywords in MARKET_TAXONOMY.items():
        for keyword in keywords:
            if i > 0:
                await asyncio.sleep(8)  # pausa tra richieste
            result = await asyncio.to_thread(
                _market_scan_keyword, keyword, categoria
            )
            results.append(result)
            total_saved += result["saved"]
            total_sold  += result["sold"]
            i += 1

    return {
        "status":        "ok",
        "keywords_scanned": i,
        "total_saved":   total_saved,
        "total_sold":    total_sold,
        "results":       results,
    }
