"""
unsubscribe.py — Endpoint per disiscrizione via token con pagina di conferma.

Flusso:
    GET  /unsubscribe?token=<uuid>  → pagina di conferma (nessuna azione)
    POST /unsubscribe?token=<uuid>  → disattivazione + pagina di successo

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


def _page(title: str, emoji: str, heading: str, body_html: str) -> str:
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
    .emoji {{ font-size: 3rem; margin-bottom: 1rem; line-height: 1; }}
    h1 {{ font-size: 1.25rem; font-weight: 700; color: #111827; margin-bottom: 0.75rem; }}
    p  {{ font-size: 0.875rem; color: #6b7280; line-height: 1.6; margin-bottom: 0.5rem; }}
    .warning {{
      background: #fef3c7;
      border: 1px solid #fde68a;
      border-radius: 8px;
      padding: 0.75rem 1rem;
      font-size: 0.8rem;
      color: #92400e;
      margin: 1.25rem 0;
      line-height: 1.5;
    }}
    .btn-confirm {{
      display: inline-block;
      background: #dc2626;
      color: #ffffff;
      border: none;
      border-radius: 8px;
      padding: 0.75rem 1.75rem;
      font-size: 0.95rem;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      margin-top: 0.5rem;
      width: 100%;
    }}
    .btn-confirm:hover {{ background: #b91c1c; }}
    .btn-cancel {{
      display: inline-block;
      margin-top: 0.75rem;
      font-size: 0.8rem;
      color: #9ca3af;
      text-decoration: none;
    }}
    .btn-cancel:hover {{ color: #6b7280; }}
    a {{ color: #2563eb; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="emoji">{emoji}</div>
    <h1>{heading}</h1>
    {body_html}
  </div>
</body>
</html>"""


def _lookup_email(token: str) -> str | None:
    """Ritorna l'email associata al token, o None se non trovata."""
    supabase = _get_supabase()
    result = (
        supabase.table("subscriptions")
        .select("email")
        .eq("unsubscribe_token", token)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0]["email"] if rows else None


def _page_not_found() -> HTMLResponse:
    body = """
      <p>Non abbiamo trovato nessuna subscription associata a questo link.</p>
      <p style="margin-top:0.5rem;">
        Se pensi sia un errore scrivici a
        <a href="mailto:ciao@lepefy.it">ciao@lepefy.it</a>.
      </p>"""
    return HTMLResponse(
        content=_page("Link non valido", "🤔", "Link non valido", body),
        status_code=404,
    )


# ── GET — pagina di conferma ──────────────────────────────────────────────────

@router.get("/unsubscribe", response_class=HTMLResponse, include_in_schema=False)
def unsubscribe_confirm(token: str = Query(...)):
    """Mostra la pagina di conferma. Non esegue ancora alcuna azione."""
    email = _lookup_email(token)
    if not email:
        return _page_not_found()

    body = f"""
      <p>Stai per disiscrivere <strong>{email}</strong> da tutte le notifiche Lepefy.</p>
      <div class="warning">
        ⚠️ Questa azione disattiverà <strong>tutte</strong> le tue alert attive.
        Per riattivarle dovrai iscriverti nuovamente su lepefy.com.
      </div>
      <form method="POST" action="/unsubscribe?token={token}">
        <button type="submit" class="btn-confirm">Sì, disiscrivi questo indirizzo</button>
      </form>
      <a href="https://www.lepefy.com" class="btn-cancel">No, torna al sito →</a>"""

    return HTMLResponse(
        content=_page("Conferma disiscrizione", "📭", "Vuoi davvero disiscriverti?", body),
        status_code=200,
    )


# ── POST — azione reale ───────────────────────────────────────────────────────

@router.post("/unsubscribe", response_class=HTMLResponse, include_in_schema=False)
def unsubscribe_execute(token: str = Query(...)):
    """Disattiva tutte le subscription dell'email associata al token."""
    email = _lookup_email(token)
    if not email:
        return _page_not_found()

    supabase = _get_supabase()
    supabase.table("subscriptions").update({"active": False}).eq("email", email).execute()

    body = f"""
      <p>
        L'indirizzo <strong>{email}</strong> non riceverà più notifiche da Lepefy.
      </p>
      <p style="margin-top:0.75rem;">
        Vuoi riscriverti in futuro? Torna su
        <a href="https://www.lepefy.com/abbonati">lepefy.com/abbonati</a>.
      </p>"""

    return HTMLResponse(
        content=_page("Disiscrizione completata", "✅", "Disiscritto con successo", body),
        status_code=200,
    )
