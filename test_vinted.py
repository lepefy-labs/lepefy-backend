"""
test_vinted.py v8 — Verifica status_ids=5 ("Non del tutto funzionante")
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


def fetch(session, keyword, status_ids=None, per_page=10):
    params = {
        "page": 1,
        "per_page": per_page,
        "search_text": keyword,
        "order": "newest_first",
        "time": int(time.time()),
    }
    if status_ids is not None:
        params["status_ids"] = status_ids
    r = session.get(VINTED_SEARCH_API, params=params, timeout=20)
    if r.status_code != 200:
        return None, r.status_code
    return r.json().get("items", []), r.status_code


def summarize(items):
    return [
        {
            "title":     i.get("title"),
            "price":     i.get("price", {}).get("amount"),
            "condition": i.get("status"),
            "url":       i.get("url"),
            "country":   i.get("user", {}).get("country_code") if isinstance(i.get("user"), dict) else None,
        }
        for i in (items or [])
    ]


def run_tests():
    session = requests.Session()
    session.headers.update(HEADERS_HOME)
    session.get(VINTED_HOME, timeout=15)
    session.headers.update(HEADERS_API)

    results = {}

    # Test 1: senza status_ids — risultati normali
    items, status = fetch(session, "iphone")
    results["iphone_no_filter"] = {
        "api_status": status,
        "count": len(items or []),
        "conditions": list({i.get("status") for i in (items or [])}),
    }
    time.sleep(1)

    # Test 2: status_ids=5 — solo "Non del tutto funzionante"
    items, status = fetch(session, "iphone", status_ids=5)
    results["iphone_status5"] = {
        "api_status": status,
        "count": len(items or []),
        "conditions": list({i.get("status") for i in (items or [])}),
        "items": summarize(items),
    }
    time.sleep(1)

    # Test 3: status_ids=5 senza keyword — quanti ce ne sono in generale?
    items, status = fetch(session, "", status_ids=5, per_page=96)
    results["all_status5"] = {
        "api_status": status,
        "count": len(items or []),
        "sample_conditions": list({i.get("status") for i in (items or [])}),
        "sample_titles": [i.get("title") for i in (items or [])[:5]],
    }

    return results
