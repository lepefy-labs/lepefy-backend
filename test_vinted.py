"""
test_vinted.py v3 — Scopre endpoint, catalog_id e struttura raw item Vinted.
Aggiungere a main.py come:
    from test_vinted import run_tests
    @app.get("/test-vinted")
    async def _(): return run_tests()
"""

import json
import time
import requests

VINTED_HOME = "https://www.vinted.it"
VINTED_API  = f"{VINTED_HOME}/api/v2/catalog/items"

HEADERS_HOME = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

HEADERS_API = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": "https://www.vinted.it/catalog",
    "X-Requested-With": "XMLHttpRequest",
}

CATALOG_IDS = [None, 3848, 2994]


def get_session():
    session = requests.Session()
    session.headers.update(HEADERS_HOME)
    r = session.get(VINTED_HOME, timeout=15)
    has_token = "access_token_web" in session.cookies
    print(f"[AUTH] home={r.status_code} | token={has_token} | cookies={list(session.cookies.keys())}")
    session.headers.update(HEADERS_API)
    return session, has_token


def fetch_raw(session, catalog_id, search_text="canon"):
    params = {
        "page": 1,
        "per_page": 5,
        "search_text": search_text,
        "order": "newest_first",
        "time": int(time.time()),
    }
    if catalog_id is not None:
        params["catalog_ids"] = catalog_id

    r = session.get(VINTED_API, params=params, timeout=20)
    ct = r.headers.get("Content-Type", "")
    is_json = "json" in ct and r.text.strip().startswith("{")

    if not is_json:
        return r.status_code, []

    data = r.json()
    return r.status_code, data.get("items", [])


def run_tests():
    print("=" * 60)
    print("TEST VINTED v3 — raw item structure discovery")
    print("=" * 60)

    session, has_token = get_session()
    time.sleep(1)

    raw_item_printed = False

    for catalog_id in CATALOG_IDS:
        label = f"catalog_id={catalog_id}"
        status, items = fetch_raw(session, catalog_id)
        print(f"\n[{label}] status={status} items={len(items)}")

        if items and not raw_item_printed:
            print(f"\n{'='*60}")
            print(f"RAW PRIMO ITEM (catalog_id={catalog_id}):")
            print(json.dumps(items[0], indent=2, ensure_ascii=False))
            print(f"{'='*60}")
            raw_item_printed = True

            # Stampa anche le chiavi di user{} se presente
            user = items[0].get("user")
            if isinstance(user, dict):
                print(f"\nCHIAVI user{{}}: {list(user.keys())}")

        time.sleep(1)

    return {"done": True, "check_logs": "Leggi i log Railway per il raw item"}


if __name__ == "__main__":
    run_tests()
