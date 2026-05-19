"""
test_vinted.py v9 — Scopre status_ids corretti per vinted.it
"""

import time
import requests

VINTED_HOME       = "https://www.vinted.it"
VINTED_SEARCH_API = f"{VINTED_HOME}/api/v2/catalog/items"

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
    session.get(VINTED_HOME, timeout=15)
    session.headers.update(HEADERS_API)

    results = {}

    # Prova status_ids da 1 a 10 senza keyword
    # Ogni ID che restituisce risultati ci dice a quale condizione corrisponde
    for sid in range(1, 11):
        params = {
            "page": 1,
            "per_page": 3,
            "search_text": "",
            "order": "newest_first",
            "time": int(time.time()),
            "status_ids": sid,
        }
        r = session.get(VINTED_SEARCH_API, params=params, timeout=20)
        items = r.json().get("items", []) if r.status_code == 200 else []
        conditions = list({i.get("status") for i in items})
        results[f"status_id_{sid}"] = {
            "count":      len(items),
            "conditions": conditions,
        }
        time.sleep(0.5)

    return results
