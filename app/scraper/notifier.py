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
    cards = ""
    for d in deals:
        score = d.get("score")
        margine = d.get("margine_stimato")
        motivazione = d.get("motivazione") or ""
        rischi = d.get("rischi") or ""
        sconto = d.get("sconto_consigliato")
        source = d.get("source", "Subito.it")
        location = d.get("location") or ""
        if location == "None" or location == "none":
            location = ""

        score_html = f"{score}/10" if score else "N/D"
        margine_html = f"+€{margine}" if margine else "N/D"
        margine_color = "#16a34a" if margine else "#9ca3af"

        # Colore bottone per piattaforma
        btn_color = "#15803d" if source == "Vinted.it" else "#dc2626"
        source_badge = "Vinted" if source == "Vinted.it" else "Subito"

        # Formato prezzo: Vinted mostra prodotto + fee su due righe
        price_raw = d.get("price_raw", "N/D")
        import re as _re
        vinted_match = _re.search(r"prodotto[:\s]+([\d\.]+).*fee[:\s]+([\d\.]+)", price_raw or "", _re.IGNORECASE)
        if vinted_match:
            prod = float(vinted_match.group(1))
            fee = float(vinted_match.group(2))
            price_display = f'''<div style="font-size:18px;font-weight:600;color:var(--text,#1a1a1a);">{prod:.2f} €</div>
            <div style="font-size:11px;color:#9ca3af;">+ €{fee:.2f} fee Vinted</div>'''
        else:
            price_display = f'<div style="font-size:18px;font-weight:600;color:#1a1a1a;">{price_raw}</div>'

        sconto_html = ""
        if sconto and margine:
            nuovo_margine = int((margine or 0) + sconto)
            sconto_html = f'''
            <div style="margin-top:8px;padding:8px 10px;background:#eff6ff;border-radius:4px;font-size:13px;color:#1d4ed8;">
              💬 Tratta: -€{sconto} → margine €{nuovo_margine}
            </div>'''

        rischi_html = ""
        if rischi:
            rischi_html = f'''
            <div style="margin-top:6px;font-size:12px;color:#dc2626;">⚠️ {rischi}</div>'''

        cards += f'''
        <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin-bottom:12px;">
          <div style="font-size:15px;font-weight:700;color:#1a1a1a;margin-bottom:4px;">
            {d["title"]}
          </div>
          <div style="font-size:12px;color:#9ca3af;margin-bottom:12px;">
            {location + " · " if location else ""}{str(d.get("date_listed",""))[:10]}
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <div>
              {price_display}
              <div style="font-size:13px;color:{margine_color};font-weight:600;">Margine: {margine_html}</div>
              <div style="font-size:12px;color:#6b7280;">Score: {score_html}</div>
            </div>
            <a href="{d["url"]}" style="display:inline-block;background:{btn_color};color:#ffffff;padding:12px 20px;border-radius:6px;font-size:14px;font-weight:600;text-decoration:none;white-space:nowrap;">
              Vedi →
            </a>
          </div>
          {f'<div style="font-size:13px;color:#555;margin-top:4px;">{motivazione}</div>' if motivazione else ""}
          {sconto_html}
          {rischi_html}
        </div>'''

    return f"""<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:16px;">
    <div style="background:#1a1a1a;border-radius:8px 8px 0 0;padding:20px 24px;margin-bottom:0;">
      <img src="https://osonphsavryefwmlhkyv.supabase.co/storage/v1/object/public/assets/lepefy-logo-email.png" alt="Lepefy" height="32" style="display:block;" />
      <div style="color:#9ca3af;font-size:13px;margin-top:6px;">nuovi affari trovati</div>
    </div>
    <div style="background:#f9fafb;padding:16px 24px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
      <p style="color:#374151;font-size:14px;margin:0;">
        Abbiamo trovato <strong>{len(deals)} annunci</strong> nella tua fascia prezzo:
      </p>
    </div>
    <div style="padding:12px 0;">
      {cards}
    </div>
    <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:0 0 8px 8px;padding:16px 24px;">
      <p style="color:#9ca3af;font-size:11px;margin:0;">
        Stai ricevendo questa email perchè sei abbonato a Lepefy Premium.<br>
        <a href="mailto:{EMAIL_FROM}" style="color:#9ca3af;">Contattaci</a> per disdire.
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
        .eq("include_defective", False)
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
        only_italy = sub.get("only_italy", True)

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
            .gte("score", 7)
            .gte("margine_stimato", 15)
            .gte("price_value", min_price)
            .lte("price_value", max_price)
            .execute()
        )
        # Filtro piattaforma applicato in Python per gestire il caso Vinted IT
        # only_italy=True  → Subito + Vinted con country=IT
        # only_italy=False → Subito + tutti gli annunci Vinted
        raw_deals = deals_response.data or []
        if only_italy:
            all_deals_pool = [
                d for d in raw_deals
                if d.get("source") == "Subito.it"
                or (d.get("source") == "Vinted.it" and d.get("country") == "IT")
            ]
        else:
            all_deals_pool = raw_deals
        all_deals = all_deals_pool

        # Filtra quelli non ancora notificati a questo utente
        new_deals = [d for d in all_deals if d["id"] not in already_notified_ids]

        # Ordina per valore atteso: score x margine
        new_deals.sort(
            key=lambda d: (d.get("score") or 0) * (d.get("margine_stimato") or 0),
            reverse=True
        )

        # Bilanciamento piattaforme + limite 5 per email
        # Max 3 deal dalla stessa piattaforma — garantisce slot a Vinted se only_italy=False
        MAX_DEALS = 5
        MAX_PER_PLATFORM = 3
        selected = []
        platform_counts: dict[str, int] = {}
        for deal in new_deals:
            source = deal.get("source", "Subito.it")
            if platform_counts.get(source, 0) < MAX_PER_PLATFORM and len(selected) < MAX_DEALS:
                selected.append(deal)
                platform_counts[source] = platform_counts.get(source, 0) + 1
        new_deals = selected

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
