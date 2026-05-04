import os
import asyncio
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", "onboarding@resend.dev")

VERDICT_COLORS = {
    "AFFARE": "#16a34a",
    "OK":     "#d97706",
    "EVITA":  "#dc2626",
    "N/D":    "#9ca3af",
}


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _verdict_badge(verdict: str) -> str:
    color = VERDICT_COLORS.get(verdict, "#9ca3af")
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:3px;'
        f'background:{color};color:#fff;font-size:11px;font-weight:700;">'
        f'{verdict}</span>'
    )


def _build_email_html(deals: list[dict]) -> str:
    rows = ""
    for d in deals:
        verdict = d.get("verdict") or "N/D"
        score = d.get("score")
        margine = d.get("margine_stimato")
        motivazione = d.get("motivazione") or ""
        rischi = d.get("rischi") or ""

        score_html = f'<strong>{score}/10</strong>' if score else "N/D"
        margine_html = f'<strong style="color:#16a34a;">+€{margine}</strong>' if margine else "N/D"
        rischi_html = (
            f'<div style="margin-top:4px;font-size:11px;color:#dc2626;">⚠ {rischi}</div>'
            if rischi else ""
        )

        rows += f"""
        <tr>
          <td style="padding:12px 8px;border-bottom:1px solid #f0f0f0;vertical-align:top;">
            <a href="{d['url']}" style="color:#1a1a1a;font-weight:600;text-decoration:none;font-size:13px;">
              {d['title']}
            </a>
            <div style="margin-top:4px;font-size:11px;color:#888;">
              {d.get('location','')} · {str(d.get('date_listed',''))[:10]}
            </div>
            {f'<div style="margin-top:6px;font-size:12px;color:#555;">{motivazione}</div>' if motivazione else ''}
            {rischi_html}
          </td>
          <td style="padding:12px 8px;border-bottom:1px solid #f0f0f0;text-align:right;vertical-align:top;white-space:nowrap;">
            <div style="font-size:18px;font-weight:700;color:#1a1a1a;">{d.get('price_raw','N/D')}</div>
            <div style="margin-top:4px;font-size:12px;color:#555;">Margine: {margine_html}</div>
            <div style="margin-top:4px;font-size:12px;color:#555;">Score: {score_html}</div>
          </td>
          <td style="padding:12px 8px;border-bottom:1px solid #f0f0f0;text-align:center;vertical-align:top;">
            <a href="{d['url']}" style="background:#1a1a1a;color:#fff;padding:6px 14px;border-radius:4px;font-size:12px;text-decoration:none;display:inline-block;">
              Vedi →
            </a>
          </td>
        </tr>"""

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="margin:0;padding:0;background:#f9f9f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
      <div style="max-width:640px;margin:40px auto;background:#fff;border-radius:8px;overflow:hidden;border:1px solid #e8e8e8;">
        <div style="background:#1a1a1a;padding:24px 32px;">
          <span style="color:#facc15;font-size:20px;font-weight:700;letter-spacing:1px;">Lepefy</span>
          <span style="color:#666;font-size:13px;margin-left:12px;">nuovi affari trovati</span>
        </div>
        <div style="padding:24px 32px;">
          <p style="color:#444;font-size:14px;margin:0 0 20px 0;">
            Abbiamo trovato <strong>{len(deals)} annunci</strong> sotto la tua soglia prezzo:
          </p>
          <table style="width:100%;border-collapse:collapse;">
            <thead>
              <tr style="background:#f5f5f5;">
                <th style="padding:8px;text-align:left;font-size:12px;color:#888;font-weight:500;">Annuncio</th>
                <th style="padding:8px;text-align:right;font-size:12px;color:#888;font-weight:500;">Prezzo & AI</th>
                <th style="padding:8px;font-size:12px;color:#888;font-weight:500;"></th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        <div style="padding:16px 32px;background:#f9f9f9;border-top:1px solid #f0f0f0;">
          <p style="color:#aaa;font-size:11px;margin:0;">
            Stai ricevendo questa email perché sei abbonato a Lepefy Premium.<br>
            <a href="mailto:{EMAIL_FROM}" style="color:#aaa;">Contattaci</a> per disdire.
          </p>
        </div>
      </div>
    </body>
    </html>"""


def _send_email(to: str, subject: str, html: str) -> None:
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": EMAIL_FROM,
            "to": [to],
            "subject": subject,
            "html": html,
        },
        timeout=15,
    )
    response.raise_for_status()


def _run_notify_job() -> dict:
    """
    1. Legge da scan_results i deal con notified=false
    2. Invia email riepilogativa con score, margine e motivazione AI
    3. Marca i deal come notified=true
    """
    supabase = _get_supabase()

    response = (
        supabase.table("scan_results")
        .select("*")
        .eq("notified", False)
        .order("score", desc=True)   # ordina per score AI decrescente
        .limit(50)
        .execute()
    )
    deals = response.data or []

    if not deals:
        return {"status": "ok", "message": "Nessun deal da notificare"}

    notify_email = os.getenv("NOTIFY_EMAIL_OVERRIDE")
    if not notify_email:
        return {"status": "error", "message": "NOTIFY_EMAIL_OVERRIDE non impostata"}

    html = _build_email_html(deals)
    subject = f"🔍 Lepefy — {len(deals)} nuovi affari trovati"

    try:
        _send_email(notify_email, subject, html)
    except Exception as e:
        return {"status": "error", "message": f"Invio email fallito: {e}"}

    ids = [d["id"] for d in deals]
    supabase.table("scan_results").update({"notified": True}).in_("id", ids).execute()

    return {
        "status": "ok",
        "notified_count": len(deals),
        "sent_to": notify_email,
    }


async def run_notify_job() -> dict:
    return await asyncio.to_thread(_run_notify_job)
