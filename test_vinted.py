"""
test_vinted.py v3 — Scopre struttura raw item Vinted.
Aggiungere a main.py:
    from test_vinted import run_tests
    @app.get("/test-vinted")
    async def _(): return run_tests()
Chiamare GET /test-vinted e leggere la response JSON nel browser.
"""

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


def run_tests():
    # Auth
    session = requests.Session()
    session.headers.update(HEADERS_HOME)
    r_home = session.get(VINTED_HOME, timeout=15)
    session.headers.update(HEADERS_API)

    # Fetch 3 item con keyword "canon", nessun filtro categoria
    params = {
        "page": 1,
        "per_page": 3,
        "search_text": "canon",
        "order": "newest_first",
        "time": int(time.time()),
    }
    r = session.get(VINTED_API, params=params, timeout=20)
    data = r.json() if "json" in r.headers.get("Content-Type", "") else {}
    items = data.get("items", [])

    return {
        "auth": {
            "home_status": r_home.status_code,
            "has_token": "access_token_web" in session.cookies,
        },
        "api_status": r.status_code,
        "item_count": len(items),
        # Raw completo dei primi 3 item — tutto nella response JSON
        "raw_items": items,
    }
