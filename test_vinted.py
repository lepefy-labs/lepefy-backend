"""
test_vinted.py v6 — Fetch HTML pagina annuncio Vinted per descrizione
"""

import re
import json
import time
import requests

VINTED_HOME = "https://www.vinted.it"
VINTED_API  = f"{VINTED_HOME}/api/v2/catalog/items"

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

HEADERS_HTML = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}


def run_tests():
    session = requests.Session()
    session.headers.update(HEADERS_HOME)
    r_home = session.get(VINTED_HOME, timeout=15)
    session.headers.update(HEADERS_API)

    # Prendi 1 item dalla search
    params = {
        "page": 1, "per_page": 3,
        "search_text": "canon eos", "order": "newest_first",
        "time": int(time.time()),
    }
    r_search = session.get(VINTED_API, params=params, timeout=20)
    items = r_search.json().get("items", []) if r_search.status_code == 200 else []

    if not items:
        return {"error": "nessun item dalla search"}

    item = items[0]
    item_url = item.get("url", "")
    item_id  = item.get("id")

    # Fetch pagina HTML annuncio — usa gli stessi cookie della sessione
    time.sleep(0.5)
    session.headers.update(HEADERS_HTML)
    r_html = session.get(item_url, timeout=20)

    html = r_html.text
    result = {
        "item_id": item_id,
        "item_url": item_url,
        "item_title": item.get("title"),
        "html_status": r_html.status_code,
        "html_length": len(html),
        "has_NEXT_DATA": "__NEXT_DATA__" in html,
        "description_found": None,
        "description_preview": None,
        "raw_keys": None,
    }

    # Strategia 1: __NEXT_DATA__ (come Subito)
    if "__NEXT_DATA__" in html:
        try:
            match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
                # Cerca description in profondità
                def find_description(obj, depth=0):
                    if depth > 8:
                        return None
                    if isinstance(obj, dict):
                        for key in ("description", "body", "details", "item_description"):
                            if key in obj and isinstance(obj[key], str) and len(obj[key]) > 10:
                                return obj[key]
                        for v in obj.values():
                            r = find_description(v, depth+1)
                            if r:
                                return r
                    elif isinstance(obj, list):
                        for el in obj[:5]:
                            r = find_description(el, depth+1)
                            if r:
                                return r
                    return None

                desc = find_description(data)
                result["description_found"] = desc is not None
                result["description_preview"] = desc[:300] if desc else None
                # Mostra le chiavi top-level di pageProps per orientarsi
                page_props = data.get("props", {}).get("pageProps", {})
                result["raw_keys"] = list(page_props.keys()) if page_props else []
        except Exception as e:
            result["next_data_error"] = str(e)

    # Strategia 2: cerca pattern JSON con "description" nel testo raw
    if not result["description_found"]:
        pattern = re.search(r'"description"\s*:\s*"([^"]{20,})"', html)
        if pattern:
            result["description_found"] = True
            result["description_preview"] = pattern.group(1)[:300]
            result["strategy"] = "regex_raw"

    return result


if __name__ == "__main__":
    import pprint
    pprint.pprint(run_tests())
