"""
test_vinted.py v5 — Verifica struttura /api/v2/items/{id}
"""

import time
import requests

VINTED_HOME = "https://www.vinted.it"
VINTED_API  = f"{VINTED_HOME}/api/v2/catalog/items"
VINTED_ITEM = f"{VINTED_HOME}/api/v2/items"

HEADERS_HOME = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

HEADERS_API = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": "https://www.vinted.it/catalog",
    "X-Requested-With": "XMLHttpRequest",
}


def run_tests():
    session = requests.Session()
    session.headers.update(HEADERS_HOME)
    r_home = session.get(VINTED_HOME, timeout=15)
    session.headers.update(HEADERS_API)

    # Prendi 3 item dalla search
    params = {
        "page": 1, "per_page": 3,
        "search_text": "canon", "order": "newest_first",
        "time": int(time.time()),
    }
    r_search = session.get(VINTED_API, params=params, timeout=20)
    items = r_search.json().get("items", []) if r_search.status_code == 200 else []

    if not items:
        return {"error": "nessun item dalla search"}

    # Chiama item API per il primo item
    item_id = items[0]["id"]
    time.sleep(0.5)
    r_item = session.get(f"{VINTED_ITEM}/{item_id}", timeout=15)

    # Estrai solo le chiavi top-level dell'oggetto item (senza foto per brevità)
    item_raw = {}
    if r_item.status_code == 200:
        full = r_item.json().get("item", {})
        # Tutte le chiavi top-level con i loro valori (escludi arrays/oggetti grandi)
        for k, v in full.items():
            if isinstance(v, (str, int, float, bool, type(None))):
                item_raw[k] = v
            elif isinstance(v, list):
                item_raw[k] = f"[array di {len(v)} elementi]"
            elif isinstance(v, dict):
                item_raw[k] = f"{{oggetto con chiavi: {list(v.keys())}}}"

    return {
        "auth": {"home_status": r_home.status_code, "has_token": "access_token_web" in session.cookies},
        "item_id_tested": item_id,
        "item_api_status": r_item.status_code,
        # Tutte le chiavi scalari — qui vediamo il nome esatto del campo descrizione
        "item_fields": item_raw,
    }
