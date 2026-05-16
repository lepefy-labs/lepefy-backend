"""
cron_trigger.py — Railway cron trigger script
Aggiungilo alla root del repo Lepefy.

Ogni servizio cron su Railway usa questo script come start command:
    python cron_trigger.py

Variabili d'ambiente richieste per ogni servizio:
    API_URL       — es. https://lepefy-production.up.railway.app
    CRON_SECRET   — token segreto (stesso di CRON_SECRET nel backend)
    CRON_ENDPOINT — es. /cron/scan
"""

import os
import sys
import httpx

API_URL = os.environ.get("API_URL", "").rstrip("/")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
CRON_ENDPOINT = os.environ.get("CRON_ENDPOINT", "")

if not API_URL or not CRON_SECRET or not CRON_ENDPOINT:
    print("ERROR: API_URL, CRON_SECRET e CRON_ENDPOINT sono obbligatori")
    sys.exit(1)

url = f"{API_URL}{CRON_ENDPOINT}"
headers = {"Authorization": f"Bearer {CRON_SECRET}"}

print(f"Triggering {url} ...")

try:
    response = httpx.get(url, headers=headers, timeout=300)
    print(f"Status: {response.status_code}")
    print(response.text)
    sys.exit(0 if response.status_code == 200 else 1)
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
