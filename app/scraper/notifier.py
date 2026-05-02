import os
import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")       # es. noreply@flipscan.it
SMTP_PASS = os.getenv("SMTP_PASS")       # app password Gmail o API key SendGrid
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _build_email_html(deals: list[dict], user_email: str) -> str:
    rows = ""
    for d in deals:
        rows += f"""
        <tr>
          <td style="padding:10px 8px;border-bottom:1px solid #f0f0f0;">
            <a href="{d['url']}" style="color:#1a1a1a;font-weight:600;text-decoration:none;">
              {d['title']}
            </a><br>
            <span style="font-size:12px;color:#888;">{d['location']} · {str(d.get('date_listed',''))[:10]}</span>
          </td>
          <td style="padding:10px 8px;border-bottom:1px solid #f0f0f0;text-align:right;white-space:nowrap;">
            <span style="font-size:18px;font-weight:700;color:#16a34a;">{d['price_raw']}</span>
          </td>
          <td style="padding:10px 8px;border-bottom:1px solid #f0f0f0;text-align:center;">
            <a href="{d['url']}" style="background:#1a1a1a;color:#fff;padding:6px 14px;border-radius:4px;font-size:12px;text-decoration:none;">
              Vedi →
            </a>
          </td>
        </tr>"""

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="margin:0;padding:0;background:#f9f9f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
      <div style="max-width:600px;margin:40px auto;background:#fff;border-radius:8px;overflow:hidden;border:1px solid #e8e8e8;">
        <div style="background:#1a1a1a;padding:24px 32px;">
          <span style="color:#facc15;font-size:18px;font-weight:700;letter-spacing:1px;">FlipScan</span>
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
                <th style="padding:8px;text-align:right;font-size:12px;color:#888;font-weight:500;">Prezzo</th>
                <th style="padding:8px;font-size:12px;color:#888;font-weight:500;"></th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        <div style="padding:16px 32px;background:#f9f9f9;border-top:1px solid #f0f0f0;">
          <p style="color:#aaa;font-size:11px;margin:0;">
            Stai ricevendo questa email perché sei abbonato a FlipScan Premium.<br>
            <a href="mailto:{EMAIL_FROM}" style="color:#aaa;">Contattaci</a> per disdire.
          </p>
        </div>
      </div>
    </body>
    </html>"""


def _send_email(to: str, subject: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(EMAIL_FROM, to, msg.as_string())


def _run_notify_job() -> dict:
    """
    1. Legge da scan_results tutti i deal non ancora notificati
    2. Li raggruppa per utente (in futuro: join con tabella users/subscriptions)
    3. Invia una email riepilogativa per utente
    4. Marca i deal come is_notified=true
    """
    supabase = _get_supabase()

    # Recupera deal non notificati
    response = (
        supabase.table("scan_results")
        .select("*")
        .eq("is_notified", False)
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    deals = response.data or []

    if not deals:
        return {"status": "ok", "message": "Nessun deal da notificare"}

    # --- Fase 1: utente singolo (hardcoded) ---
    # Quando integri Supabase Auth, sostituisci con:
    # users = supabase.table("subscriptions").select("email").eq("plan","premium").execute()
    # e raggruppa i deals per keyword/preferenze utente
    notify_email = os.getenv("NOTIFY_EMAIL_OVERRIDE")
    if not notify_email:
        return {"status": "error", "message": "NOTIFY_EMAIL_OVERRIDE non impostata"}

    html = _build_email_html(deals, notify_email)
    subject = f"🔍 FlipScan — {len(deals)} nuovi affari trovati"

    try:
        _send_email(notify_email, subject, html)
    except Exception as e:
        return {"status": "error", "message": f"Invio email fallito: {e}"}

    # Marca come notificati
    ids = [d["id"] for d in deals]
    supabase.table("scan_results").update({"is_notified": True}).in_("id", ids).execute()

    return {
        "status": "ok",
        "notified_count": len(deals),
        "sent_to": notify_email,
    }


async def run_notify_job() -> dict:
    return await asyncio.to_thread(_run_notify_job)
