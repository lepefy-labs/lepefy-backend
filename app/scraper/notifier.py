import os
import asyncio
import httpx
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", "noreply@tuodominio.it")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "Lepefy")


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _build_email_html(deals: list[dict]) -> str:
    rows = ""
    for d in deals:
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
            {f'<div style="margin-top:4px;font-size:12px;color:#2563eb;">Tratta: -€{d["sconto_consigliato"]} → margine €{int((d["margine_stimato"] or 0) + d["sconto_consigliato"])}</div>' if d.get("sconto_consigliato") else ""}
            {f'<div style="margin-top:4px;font-size:11px;color:#9ca3af;">eBay: €{d["ebay_valore_mercato"]}</div>' if d.get("ebay_valore_mercato") else ""}
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
          <img src="https://osonphsavryefwmlhkyv.supabase.co/storage/v1/object/public/assets/lepefy-logo-email.png" alt="Lepefy" height="40" style="display:block;" />
          <span style="color:#666;font-size:13px;margin-top:8px;display:block;">nuovi affari trovati</span>
        </div>
        <div style="padding:24px 32px;">
          <p style="color:#444;font-size:14px;margin:0 0 20px 0;">
            Abbiamo trovato <strong>{len(deals)} annunci</strong> nella tua fascia prezzo:
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
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "api-key": BREVO_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "sender": {"name": EMAIL_FROM_NAME, "email": EMAIL_FROM},
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": html,
        },
        timeout=15,
    )
    response.raise_for_status()


def _run_notify_job() -> dict:
    """
    Per ogni subscription attiva:
    1. Trova i deal nel pool scan_results che rientrano nella fascia prezzo
       e che non sono ancora stati notificati a questo utente
    2. Invia email riepilogativa via Brevo
    3. Registra le notifiche inviate in notifications_log
    """
    supabase = _get_supabase()

    subs_response = (
        supabase.table("subscriptions")
        .select("*")
        .eq("active", True)
        .execute()
    )
    subscriptions = subs_response.data or []

    if not subscriptions:
        return {"status": "ok", "message": "Nessuna subscription attiva"}

    total_sent = 0
    results = []

    for sub in subscriptions:
        sub_id = sub["id"]
        email = sub["email"]
        keyword = sub["keyword"].lower()
        min_price = sub.get("min_threshold", 0)
        max_price = sub["max_threshold"]

        # Deal già notificati a questo utente
        notified_response = (
            supabase.table("notifications_log")
            .select("scan_result_id")
            .eq("subscription_id", sub_id)
            .execute()
        )
        already_notified_ids = {
            row["scan_result_id"] for row in (notified_response.data or [])
        }

        # Deal nel pool che rientrano nella fascia prezzo
        deals_response = (
            supabase.table("scan_results")
            .select("*")
            .ilike("keyword", keyword)
            .eq("scored", True)
            .gte("score", 2)
            .gte("margine_stimato", 15)
            .gte("price_value", min_price)
            .lte("price_value", max_price)
            .order("score", desc=True)
            .limit(50)
            .execute()
        )
        all_deals = deals_response.data or []

        # Filtra quelli non ancora notificati a questo utente
        new_deals = [d for d in all_deals if d["id"] not in already_notified_ids]

        # Ordina per valore atteso: score x margine
        # Un affare con score 8 e margine 150 (1200) batte score 8 e margine 20 (160)
        new_deals.sort(
            key=lambda d: (d.get("score") or 0) * (d.get("margine_stimato") or 0),
            reverse=True
        )

        # Limita a 5 annunci per email — i restanti verranno inviati al ciclo successivo
        new_deals = new_deals[:5]

        if not new_deals:
            results.append({"email": email, "keyword": keyword, "sent": 0})
            continue

        html = _build_email_html(new_deals)
        subject = f"🔍 Lepefy — {len(new_deals)} nuovi affari su {keyword}"

        try:
            _send_email(email, subject, html)
        except Exception as e:
            results.append({"email": email, "keyword": keyword, "error": str(e)})
            continue

        # Registra le notifiche inviate
        log_rows = [
            {"subscription_id": sub_id, "scan_result_id": d["id"]}
            for d in new_deals
        ]
        supabase.table("notifications_log").insert(log_rows).execute()

        total_sent += len(new_deals)
        results.append({
            "email": email,
            "keyword": keyword,
            "sent": len(new_deals),
        })

    return {
        "status": "ok",
        "subscriptions_processed": len(subscriptions),
        "total_deals_notified": total_sent,
        "details": results,
    }


async def run_notify_job() -> dict:
    return await asyncio.to_thread(_run_notify_job)
