"""
test_vinted.py v2 — Test integrazione Vinted per Lepefy
Aggiungere a main.py come: from test_vinted import run_tests
                             @app.get("/test-vinted")
                             async def _(): return run_tests()
"""

import json
import time
import requests

VINTED_HOME = "https://www.vinted.it"

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

# Endpoint candidati da testare
ENDPOINTS = [
    "/web/api/core/catalog/items",
    "/api/v2/catalog/items",
    "/api/v2/items",
]

# catalog_ids candidati per vinted.it
# None = solo search_text, massima probabilita' di risultati
CATALOG_IDS_CANDIDATES = [
    None,
    3848,   # fotografia (da URL vinted.it/catalog/3848-photography)
    2994,   # elettronica (da URL vinted.it/catalog/2994-electronics)
    1920,   # valore trovato in articolo reverse engineering
]


def get_session():
    """Crea sessione con cookie Datadome validi."""
    session = requests.Session()
    session.headers.update(HEADERS_HOME)
    r = session.get(VINTED_HOME, timeout=15)
    cookies = dict(session.cookies)
    has_token = "access_token_web" in cookies
    print(f"  Home: {r.status_code} | token: {has_token} | cookies: {list(cookies.keys())}")
    return session, has_token


def try_endpoint(session, endpoint, catalog_id, search_text="canon"):
    """Prova un endpoint con un catalog_id specifico."""
    params = {
        "page": 1,
        "per_page": 20,
        "search_text": search_text,
        "order": "newest_first",
        "time": int(time.time()),
    }
    if catalog_id is not None:
        params["catalog_ids"] = catalog_id

    url = VINTED_HOME + endpoint
    session.headers.update(HEADERS_API)

    try:
        r = session.get(url, params=params, timeout=15)
        ct = r.headers.get("Content-Type", "")
        is_json = "json" in ct and r.text.strip().startswith("{")

        raw_items = []
        items = []
        if is_json:
            data = r.json()
            raw_items = data.get("items", [])
            if raw_items:
              import json
              print("RAW PRIMO ITEM:")
              print(json.dumps(raw_items[0], indent=2, ensure_ascii=False))
            for it in raw_items[:3]:
                items.append({
                    "id": it.get("id"),
                    "title": it.get("title"),
                    "price": it.get("price"),
                    "total_item_price": it.get("total_item_price"),
                    "service_fee": it.get("service_fee"),
                    "currency": it.get("currency"),
                    "brand": it.get("brand_title"),
                    "condition": it.get("status"),
                    "url": it.get("url"),
                })

        return {
            "endpoint": endpoint,
            "catalog_id": catalog_id,
            "status": r.status_code,
            "content_type": ct,
            "is_json": is_json,
            "item_count": len(raw_items) if is_json else 0,
            "items_preview": items,
            "raw_preview": r.text[:300] if not is_json else "",
        }
    except Exception as e:
        return {
            "endpoint": endpoint,
            "catalog_id": catalog_id,
            "status": None,
            "error": str(e),
            "is_json": False,
            "item_count": 0,
            "items_preview": [],
        }


def run_tests():
    results = {"session": {}, "endpoint_tests": [], "winner": None}

    print("=" * 60)
    print("TEST VINTED v2 - endpoint + catalog_id discovery")
    print("=" * 60)

    print("\n[AUTH] Ottengo cookie da vinted.it...")
    session, has_token = get_session()
    results["session"]["has_token"] = has_token

    if not has_token:
        print("  WARN: Nessun token - Datadome potrebbe bloccare le API calls.")

    time.sleep(1)

    print("\n[SCAN] Test combinazioni endpoint / catalog_id...")
    for endpoint in ENDPOINTS:
        for catalog_id in CATALOG_IDS_CANDIDATES:
            label = f"{endpoint} | catalog={catalog_id}"
            res = try_endpoint(session, endpoint, catalog_id)
            results["endpoint_tests"].append(res)

            if res["item_count"] > 0:
                icon = "OK"
            elif res["status"] == 200:
                icon = "200-no-items"
            else:
                icon = "FAIL"

            print(f"  [{icon}] {label} -> status={res['status']} json={res['is_json']} items={res['item_count']}")

            if res["item_count"] > 0 and results["winner"] is None:
                results["winner"] = res
                print(f"\n  WINNER: {label}")
                print(f"  Primo item: {json.dumps(res['items_preview'][0], ensure_ascii=False, indent=2)}")

            time.sleep(0.5)

    print("\n" + "=" * 60)
    if results["winner"]:
        w = results["winner"]
        print(f"Endpoint funzionante: {w['endpoint']}")
        print(f"catalog_id: {w['catalog_id']}")
        print(f"Items trovati: {w['item_count']}")
    else:
        print("Nessun endpoint ha restituito items.")
        print("Suggerimento: apri DevTools su vinted.it/catalog,")
        print("filtra Network per 'catalog/items', copia l'URL esatto.")

    return results


if __name__ == "__main__":
    run_tests()
