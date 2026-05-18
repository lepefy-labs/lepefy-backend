"""
test_vinted.py v7 — Debug estrazione body da URL specifico
"""

import json
import requests

VINTED_HOME = "https://www.vinted.it"

HEADERS_HOME = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

TARGET_URL = "https://www.vinted.it/items/8331963288-olympus-om-101"
KEY = '"description":'


def run_tests():
    session = requests.Session()
    session.headers.update(HEADERS_HOME)
    session.get(VINTED_HOME, timeout=15)  # ottieni cookie

    r = session.get(TARGET_URL, timeout=25)
    html = r.text
    decoder = json.JSONDecoder()

    # Trova TUTTE le occorrenze di "description": nell'HTML
    occurrences = []
    search_start = 0
    while True:
        idx = html.find(KEY, search_start)
        if idx == -1:
            break

        # Trova il valore
        val_start = idx + len(KEY)
        while val_start < len(html) and html[val_start] in " \t\n\r":
            val_start += 1

        value = None
        value_type = None
        try:
            if val_start < len(html):
                value, _ = decoder.raw_decode(html, val_start)
                value_type = type(value).__name__
        except Exception as e:
            value_type = f"parse_error: {e}"

        occurrences.append({
            "position": idx,
            "value_type": value_type,
            "value_preview": str(value)[:200] if value is not None else None,
            "value_length": len(str(value)) if isinstance(value, str) else None,
        })

        search_start = idx + 1
        if len(occurrences) >= 10:  # max 10 per non appesantire il response
            break

    return {
        "url": TARGET_URL,
        "html_status": r.status_code,
        "html_length": len(html),
        "key_occurrences_found": len(occurrences),
        "occurrences": occurrences,
    }
