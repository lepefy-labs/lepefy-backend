"""
test_vinted.py v4 — Verifica struttura /api/v2/users/{user_id}
Aggiungere a main.py:
    from test_vinted import run_tests
    @app.get("/test-vinted")
    async def _(): return run_tests()
"""

import time
import requests

VINTED_HOME = "https://www.vinted.it"
VINTED_API  = f"{VINTED_HOME}/api/v2/catalog/items"
VINTED_USER = f"{VINTED_HOME}/api/v2/users"

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

    # Step 1: fetch 3 item per ricavare user_id reali
    params = {
        "page": 1,
        "per_page": 3,
        "search_text": "canon",
        "order": "newest_first",
        "time": int(time.time()),
    }
    r_search = session.get(VINTED_API, params=params, timeout=20)
    items = r_search.json().get("items", []) if r_search.status_code == 200 else []

    # Raccogli user_id univoci dai risultati
    user_ids = list({
        item["user"]["id"]
        for item in items
        if isinstance(item.get("user"), dict) and item["user"].get("id")
    })

    # Step 2: chiama /api/v2/users/{id} per i primi 2 user_id trovati
    user_responses = {}
    for uid in user_ids[:2]:
        time.sleep(0.5)
        r_user = session.get(f"{VINTED_USER}/{uid}", timeout=15)
        user_responses[str(uid)] = {
            "status": r_user.status_code,
            # Raw completo nella response — leggi nel browser
            "raw": r_user.json() if r_user.status_code == 200 else r_user.text[:300],
        }

    return {
        "auth": {
            "home_status": r_home.status_code,
            "has_token": "access_token_web" in session.cookies,
        },
        "search_status": r_search.status_code,
        "user_ids_found": user_ids,
        # Raw completo delle risposte user — contiene paese e città se disponibili
        "user_api_responses": user_responses,
    }
