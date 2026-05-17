"""
test_vinted.py — Test integrazione Vinted per Lepefy
Aggiungere temporaneamente a main.py come endpoint GET /test-vinted
oppure eseguire standalone con: python test_vinted.py

Testa tre approcci in sequenza e stampa quale funziona.
"""

import os
import json
import time
import urllib.parse
import requests

SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "")

# Endpoint interno Vinted
VINTED_BASE = "https://www.vinted.it/web/api/core/catalog/items"

# Parametri di ricerca: fotocamere Canon, categoria fotografia
TEST_PARAMS = {
    "page": 1,
    "per_page": 20,
    "search_text": "canon",
    "catalog_ids": 3848,   # fotografia
    "order": "newest_first",
}


# ─── Approccio 1: ScraperAPI (JSON endpoint, no render) ──────────────────────

def test_scraperapi_json():
    """ScraperAPI su endpoint JSON Vinted — il più veloce se funziona."""
    if not SCRAPERAPI_KEY:
        return None, "SCRAPERAPI_KEY non settata"

    target_url = VINTED_BASE + "?" + urllib.parse.urlencode(TEST_PARAMS)
    proxy_url = (
        f"http://scraperapi:{SCRAPERAPI_KEY}@proxy-server.scraperapi.com:8001"
    )
    proxies = {"http": proxy_url, "https": proxy_url}

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Accept": "application/json",
        "Accept-Language": "it-IT,it;q=0.9",
        "Referer": "https://www.vinted.it/",
    }

    try:
        r = requests.get(target_url, headers=headers, proxies=proxies, timeout=30, verify=False)
        return r.status_code, r.headers.get("Content-Type", ""), r.text[:2000]
    except Exception as e:
        return None, str(e), ""


# ─── Approccio 2: ScraperAPI via query param (metodo alternativo) ─────────────

def test_scraperapi_queryparam():
    """ScraperAPI come query param — stesso risultato ma auth diversa."""
    if not SCRAPERAPI_KEY:
        return None, "SCRAPERAPI_KEY non settata", ""

    target_url = VINTED_BASE + "?" + urllib.parse.urlencode(TEST_PARAMS)
    sa_url = "http://api.scraperapi.com"
    params = {
        "api_key": SCRAPERAPI_KEY,
        "url": target_url,
        "country_code": "it",
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Accept": "application/json",
        "Accept-Language": "it-IT,it;q=0.9",
    }

    try:
        r = requests.get(sa_url, params=params, headers=headers, timeout=30)
        return r.status_code, r.headers.get("Content-Type", ""), r.text[:2000]
    except Exception as e:
        return None, str(e), ""


# ─── Approccio 3: cookie auth diretta (senza proxy) ──────────────────────────

def test_direct_with_cookies():
    """
    Replica il flusso browser: prima GET alla home per ottenere
    access_token_web dai cookie, poi chiama l'API.
    Funziona solo se Datadome non blocca l'IP di Railway.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9",
    })

    try:
        # Step 1: visita home per ottenere token
        r_home = session.get("https://www.vinted.it", timeout=15)
        cookies = dict(session.cookies)
        has_token = "access_token_web" in cookies

        # Step 2: chiama API con i cookie ottenuti
        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.vinted.it/catalog/3848-photography",
            "X-Requested-With": "XMLHttpRequest",
        })
        r_api = session.get(VINTED_BASE, params=TEST_PARAMS, timeout=15)

        return (
            r_home.status_code,
            r_api.status_code,
            has_token,
            r_api.headers.get("Content-Type", ""),
            r_api.text[:2000]
        )
    except Exception as e:
        return None, None, False, str(e), ""


# ─── Parser risposta ──────────────────────────────────────────────────────────

def parse_items(raw_text):
    """Prova a estrarre items dalla risposta JSON."""
    try:
        data = json.loads(raw_text)
        items = data.get("items", [])
        if not items:
            return []
        # Campi utili per Lepefy
        parsed = []
        for it in items[:3]:
            parsed.append({
                "id": it.get("id"),
                "title": it.get("title"),
                "price_value": it.get("price"),            # prezzo venditore
                "total_item_price": it.get("total_item_price"),  # prezzo acquirente (con fee)
                "service_fee": it.get("service_fee"),      # fee acquirente
                "currency": it.get("currency"),
                "brand": it.get("brand_title"),
                "condition": it.get("status"),
                "url": it.get("url"),
                "photo": it.get("photo", {}).get("url") if it.get("photo") else None,
            })
        return parsed
    except Exception as e:
        return [{"parse_error": str(e)}]


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_tests():
    results = {}

    print("=" * 60)
    print("TEST VINTED INTEGRATION — Lepefy")
    print("=" * 60)

    # Test 1
    print("\n[1/3] ScraperAPI via proxy string...")
    status, ct, body = test_scraperapi_json()
    results["scraperapi_proxy"] = {
        "status": status, "content_type": ct,
        "is_json": "json" in str(ct).lower(),
        "items": parse_items(body) if "json" in str(ct).lower() else [],
        "raw_preview": body[:300],
    }
    print(f"  Status: {status} | Content-Type: {ct}")
    if results["scraperapi_proxy"]["items"]:
        print(f"  ✅ Items parsati: {len(results['scraperapi_proxy']['items'])}")
        print(f"  Primo item: {results['scraperapi_proxy']['items'][0]}")
    else:
        print(f"  ❌ Nessun item | Preview: {body[:200]}")

    time.sleep(2)

    # Test 2
    print("\n[2/3] ScraperAPI via query param...")
    status, ct, body = test_scraperapi_queryparam()
    results["scraperapi_queryparam"] = {
        "status": status, "content_type": ct,
        "is_json": "json" in str(ct).lower(),
        "items": parse_items(body) if "json" in str(ct).lower() else [],
        "raw_preview": body[:300],
    }
    print(f"  Status: {status} | Content-Type: {ct}")
    if results["scraperapi_queryparam"]["items"]:
        print(f"  ✅ Items parsati: {len(results['scraperapi_queryparam']['items'])}")
    else:
        print(f"  ❌ Nessun item | Preview: {body[:200]}")

    time.sleep(2)

    # Test 3
    print("\n[3/3] Chiamata diretta con cookie session...")
    home_status, api_status, has_token, ct, body = test_direct_with_cookies()
    results["direct_cookies"] = {
        "home_status": home_status, "api_status": api_status,
        "has_token": has_token, "content_type": ct,
        "is_json": "json" in str(ct).lower(),
        "items": parse_items(body) if "json" in str(ct).lower() else [],
        "raw_preview": body[:300],
    }
    print(f"  Home status: {home_status} | Token: {has_token} | API status: {api_status} | CT: {ct}")
    if results["direct_cookies"]["items"]:
        print(f"  ✅ Items parsati: {len(results['direct_cookies']['items'])}")
    else:
        print(f"  ❌ Nessun item | Preview: {body[:200]}")

    # Riepilogo
    print("\n" + "=" * 60)
    print("RIEPILOGO")
    print("=" * 60)
    for approach, res in results.items():
        status = res.get("api_status") or res.get("status")
        ok = bool(res.get("items"))
        print(f"  {'✅' if ok else '❌'} {approach}: status={status}, json={res['is_json']}, items={len(res.get('items', []))}")

    return results


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")  # sopprime warning SSL
    run_tests()
