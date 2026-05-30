"""
notifier_defective.py — Notifiche aggregate per annunci Vinted difettosi.

Una sola email per utente (aggregata su tutte le sue subscription con
include_defective=True) con i 5 articoli più economici in assoluto.

Gli annunci difettosi hanno scored=True all'inserimento (bypass scorer),
quindi score e margine_stimato sono NULL — ordinamento per prezzo crescente.

Prerequisiti DB:
    ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS include_defective boolean DEFAULT false;
"""

import os
import re
import asyncio
import httpx
from collections import defaultdict
from supabase import create_client, Client

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
BREVO_API_KEY        = os.getenv("BREVO_API_KEY")
EMAIL_CONTACT        = os.getenv("EMAIL_CONTACT", "ciao@lepefy.it")
EMAIL_FROM           = os.getenv("EMAIL_FROM", "noreply@lepefy.it")
EMAIL_FROM_NAME      = os.getenv("EMAIL_FROM_NAME", "Lepefy")

DEFECTIVE_CONDITION  = "Non del tutto funzionante"
MAX_DEALS            = 5


# ──────────────────────────────────────────────
# Supabase
# ──────────────────────────────────────────────

def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ──────────────────────────────────────────────
# Email template
# ──────────────────────────────────────────────

def _condition_badge(condition: str) -> str:
    if condition == "Non del tutto funzionante":
        color, bg, icon = "#991b1b", "#fee2e2", "ti-tool"
    else:
        color, bg, icon = "#92400e", "#fef3c7", "ti-alert-triangle"
    return (
        f'<span style="display:inline-block;padding:3px 8px;border-radius:4px;'
        f'font-size:11px;font-weight:600;color:{color};background:{bg};">'
        f'<i class="ti {icon}" aria-hidden="true" style="font-size:11px;'
        f'vertical-align:-1px;margin-right:3px;"></i>{condition}</span>'
    )


def _build_defective_email_html(deals: list[dict]) -> str:
    cards = ""
    for d in deals:
        title     = d.get("title", "N/D")
        url       = d.get("url", "#")
        condition = d.get("condition", "")
        location  = d.get("location") or ""
        country   = d.get("country") or ""
        body      = d.get("body") or ""
        image_url = d.get("image_url") or ""
        price_raw = d.get("price_raw", "N/D")

        vinted_match = re.search(
            r"([\d\.]+)\s*EUR.*prodotto[:\s]+([\d\.]+).*fee[:\s]+([\d\.]+)",
            price_raw or "", re.IGNORECASE
        )
        if vinted_match:
            total = float(vinted_match.group(1))
            prod  = float(vinted_match.group(2))
            fee   = float(vinted_match.group(3))
            price_display = (
                f'<div style="font-size:17px;font-weight:500;'
                f'color:var(--text,#1a1a1a);">{total:.2f} €</div>'
                f'<div style="font-size:11px;color:#9ca3af;">'
                f'prodotto {prod:.2f} € + fee {fee:.2f} €</div>'
            )
        else:
            price_display = (
                f'<div style="font-size:17px;font-weight:500;'
                f'color:#1a1a1a;">{price_raw}</div>'
            )

        location_str = location
        if country and country != "IT" and country not in location_str:
            location_str = f"{location_str} · {country}".strip(" · ")

        body_preview = ""
        if body:
            body_clean = body.replace("\n", " ").strip()
            excerpt    = (body_clean[:180] + "…") if len(body_clean) > 180 else body_clean
            body_preview = (
                f'<div style="font-size:11px;color:#6b7280;margin-top:8px;'
                f'font-style:italic;border-top:0.5px solid #e5e7eb;'
                f'padding-top:8px;">"{excerpt}"</div>'
            )

        img_html = ""
        if image_url:
            img_html = (
                f'<img src="{image_url}" alt="" '
                f'style="width:100%;height:140px;object-fit:cover;'
                f'border-radius:6px;margin-bottom:10px;display:block;" />'
            )

        cards += f'''
        <div style="background:#ffffff;border:1px solid #e5e7eb;
                    border-radius:8px;padding:14px;margin-bottom:10px;">
          {img_html}
          <div style="font-size:14px;font-weight:600;color:#1a1a1a;
                      margin-bottom:6px;">{title}</div>
          <div style="margin-bottom:6px;">{_condition_badge(condition)}</div>
          <div style="font-size:11px;color:#9ca3af;margin-bottom:10px;">
            {location_str}
          </div>
          <div style="display:flex;justify-content:space-between;
                      align-items:center;">
            <div>{price_display}</div>
            <a href="{url}"
               style="display:inline-block;background:#15803d;color:#ffffff;
                      padding:9px 16px;border-radius:6px;font-size:13px;
                      font-weight:600;text-decoration:none;white-space:nowrap;">
              Vedi →
            </a>
          </div>
          {body_preview}
        </div>'''

    return f"""<!DOCTYPE html>
<html>
<head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f3f4f6;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:16px;">
    <div style="background:#1a1a1a;border-radius:8px 8px 0 0;padding:20px 24px;">
      <img src="https://osonphsavryefwmlhkyv.supabase.co/storage/v1/object/public/assets/lepefy-logo-email.png"
           alt="Lepefy" height="32" style="display:block;" />
      <div style="color:#9ca3af;font-size:13px;margin-top:6px;">
        articoli da riparare trovati su Vinted
      </div>
    </div>
    <div style="background:#f9fafb;padding:16px 24px;
                border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
      <p style="color:#374151;font-size:14px;margin:0;">
        Abbiamo trovato <strong>{len(deals)} articoli</strong>
        in condizioni Discrete o Non del tutto funzionante
        nella tua fascia prezzo:
      </p>
    </div>
    <div style="padding:12px 0;">{cards}</div>
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;
                border-radius:8px;padding:16px 20px;margin-bottom:10px;
                text-align:center;">
      <p style="color:#166534;font-size:13px;margin:0 0 4px;">
        🔧 Altri articoli difettosi e occasioni per riparatori su Lepefy.
      </p>
      <p style="color:#16a34a;font-size:11px;margin:0 0 10px;">
        Sul sito i deal pubblici arrivano con 12 ore di ritardo —
        tu li hai già visti per primo.
      </p>
      <a href="https://www.lepefy.com/deals"
         style="display:inline-block;background:#15803d;color:#ffffff;
                padding:9px 20px;border-radius:6px;font-size:13px;
                font-weight:600;text-decoration:none;">
        Esplora i deal →
      </a>
    </div>
    <div style="background:#f9fafb;border:1px solid #e5e7eb;
                border-radius:0 0 8px 8px;padding:16px 24px;">
      <p style="color:#9ca3af;font-size:11px;margin:0;">
        Stai ricevendo questa email perché sei abbonato a Lepefy.<br>
        <a href="mailto:{EMAIL_CONTACT}" style="color:#9ca3af;">Contattaci</a> per disdire.
      </p>
    </div>
  </div>
</body>
</html>"""


# ──────────────────────────────────────────────
# Invio email
# ──────────────────────────────────────────────

def _send_email(to: str, subject: str, html: str) -> None:
    response = httpx.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
        json={
            "sender":      {"name": EMAIL_FROM_NAME, "email": EMAIL_FROM},
            "to":          [{"email": to}],
            "subject":     subject,
            "htmlContent": html,
        },
        timeout=15,
    )
    response.raise_for_status()


# ──────────────────────────────────────────────
# Notify job
# ──────────────────────────────────────────────

def _run_defective_notify_job() -> dict:
    """
    Per ogni utente (email unica) con subscription include_defective=True:
    1. Aggrega i deal difettosi da tutte le sue subscription attive
    2. Esclude già notificati, applica filtri prezzo e paese
    3. Ordina per prezzo crescente, prende i top 5 globali
    4. Invia una sola email aggregata
    5. Registra in notifications_log per ogni deal, contro la subscription
       di appartenenza (per dedup corretto ai cicli successivi)
    """
    supabase = _get_supabase()

    subs_response = (
        supabase.table("subscriptions")
        .select("*")
        .eq("active", True)
        .eq("include_defective", True)
        .execute()
    )
    subscriptions = subs_response.data or []

    if not subscriptions:
        return {"status": "ok", "message": "Nessuna subscription con include_defective=true"}

    # Raggruppa subscription per email
    by_email: dict[str, list[dict]] = defaultdict(list)
    for sub in subscriptions:
        by_email[sub["email"]].append(sub)

    total_sent = 0
    results    = []

    for email, user_subs in by_email.items():
        only_italy = any(sub.get("only_italy", True) for sub in user_subs)

        # Deal già notificati a questo utente (su tutte le sue subscription)
        sub_ids = [sub["id"] for sub in user_subs]
        notified_response = (
            supabase.table("notifications_log")
            .select("scan_result_id")
            .in_("subscription_id", sub_ids)
            .execute()
        )
        already_notified_ids = {
            row["scan_result_id"] for row in (notified_response.data or [])
        }

        # Raccogli deal da ogni subscription, tenendo traccia della sub di appartenenza
        # deal_map: scan_result_id → (deal_dict, subscription_id)
        deal_map: dict[str, tuple[dict, str]] = {}

        for sub in user_subs:
            keyword   = sub["keyword"].lower()
            min_price = sub.get("min_threshold") or 0
            max_price = sub["max_threshold"]

            deals_response = (
                supabase.table("scan_results")
                .select("*")
                .ilike("keyword", keyword)
                .eq("source", "Vinted.it")
                .eq("scored", True)
                .eq("condition", DEFECTIVE_CONDITION)
                .gte("price_value", min_price)
                .lte("price_value", max_price)
                .execute()
            )
            for deal in (deals_response.data or []):
                deal_id = deal["id"]
                if deal_id in already_notified_ids:
                    continue
                if only_italy and deal.get("country") != "IT":
                    continue
                # Se lo stesso deal è coperto da più subscription, tieni la prima
                if deal_id not in deal_map:
                    deal_map[deal_id] = (deal, sub["id"])

        if not deal_map:
            results.append({"email": email, "sent": 0})
            continue

        # Ordina per prezzo crescente, prendi top 5
        all_deals = sorted(
            deal_map.values(),
            key=lambda x: x[0].get("price_value") or 0
        )[:MAX_DEALS]

        selected_deals = [d for d, _ in all_deals]
        html    = _build_defective_email_html(selected_deals)
        subject = f"🔧 Lepefy — {len(selected_deals)} articoli da riparare su Vinted"

        try:
            _send_email(email, subject, html)
        except Exception as e:
            results.append({"email": email, "error": str(e)})
            continue

        # Registra in notifications_log contro la subscription di appartenenza
        log_rows = [
            {"subscription_id": sub_id, "scan_result_id": deal["id"]}
            for deal, sub_id in all_deals
        ]
        supabase.table("notifications_log").insert(log_rows).execute()

        total_sent += len(selected_deals)
        results.append({"email": email, "sent": len(selected_deals)})

    return {
        "status":             "ok",
        "users_processed":    len(by_email),
        "total_deals_notified": total_sent,
        "details":            results,
    }


async def run_defective_notify_job() -> dict:
    return await asyncio.to_thread(_run_defective_notify_job)
