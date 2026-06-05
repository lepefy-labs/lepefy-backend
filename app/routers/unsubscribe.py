"""
unsubscribe.py — Endpoint per disiscrizione via token.

GET /unsubscribe?token=<uuid>

Disattiva TUTTE le subscription associate all'email del token
(unsubscribe globale per email, non per singola subscription).

Aggiungere questo router in main.py:
    from app.routers.unsubscribe import router as unsubscribe_router
    app.include_router(unsubscribe_router)
"""

import os
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from supabase import create_client

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

router = APIRouter()


def _get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _page(title: str, emoji: str, heading: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} · Lepefy</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #f3f4f6;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 2rem;
    }}
    .card {{
      background: #ffffff;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      padding: 2.5rem 2rem;
      max-width: 440px;
      width: 100%;
      text-align: center;
      box-shadow: 0 1px 6px rgba(0,0,0,.06);
    }}
    .emoji {{ font-size: 3rem; margin-bottom: 1rem; }}
    h1 {{ font-size: 1.25rem; font-weight: 700; color: #111827; margin-bottom: 0.75rem; }}
    p  {{ font-size: 0.875rem; color: #6b7280; line-height: 1.6; }}
    a  {{ color: #2563eb; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="emoji">{emoji}</div>
    <h1>{heading}</h1>
    <p>{body}</p>
  </div>
</body>
</html>"""


@router.get("/unsubscribe", response_class=HTMLResponse, include_in_schema=False)
def unsubscribe(token: str = Query(..., description="UUID token di disiscrizione")):
    """
    Disattiva tutte le subscription dell'email associata al token.
    Risponde con una pagina HTML (nessun JSON — è un link da email).
    """
    supabase = _get_supabase()

    # 1. Trova la subscription con questo token
    result = (
        supabase.table("subscriptions")
        .select("email")
        .eq("unsubscribe_token", token)
        .limit(1)
        .execute()
    )
    rows = result.data or []

    if not rows:
        return HTMLResponse(
            content=_page(
                "Link non valido", "🤔",
                "Link non valido o già utilizzato",
                "Non abbiamo trovato nessuna subscription associata a questo link.<br>"
                "Se pensi sia un errore scrivici a "
                '<a href="mailto:ciao@lepefy.it">ciao@lepefy.it</a>.'
            ),
            status_code=404,
        )

    email = rows[0]["email"]

    # 2. Disattiva TUTTE le subscription di quell'email
    supabase.table("subscriptions").update({"active": False}).eq("email", email).execute()

    return HTMLResponse(
        content=_page(
            "Disiscrizione completata", "✅",
            "Disiscritto con successo",
            f"L'indirizzo <strong>{email}</strong> non riceverà più notifiche da Lepefy.<br><br>"
            "Vuoi riscriverti in futuro? Torna su "
            '<a href="https://www.lepefy.com/abbonati">lepefy.com/abbonati</a>.'
        ),
        status_code=200,
    )
