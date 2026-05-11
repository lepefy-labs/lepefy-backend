"""
content_generator.py
Legge i top deal delle ultime 24h da scan_results,
genera caption + hashtag per Instagram, TikTok e Facebook
tramite Claude Haiku, invia tutto via email con Brevo
e logga i token consumati su ai_usage_log.
"""

import os
import json
import httpx
from datetime import datetime, timezone, timedelta

# ─── Config ───────────────────────────────────────────────────────────────────

SUPABASE_URL         = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]
BREVO_API_KEY        = os.environ["BREVO_API_KEY"]
EMAIL_FROM           = os.environ["EMAIL_FROM"]
EMAIL_FROM_NAME      = os.environ.get("EMAIL_FROM_NAME", "Lepefy")
CONTENT_EMAIL_TO     = os.environ["CONTENT_EMAIL_TO"]

TOP_N_DEALS    = 3
MIN_SCORE      = 6
HOURS_LOOKBACK = 24

# Prezzi Haiku ($ per milione di token)
HAIKU_INPUT_PRICE  = 0.80 / 1_000_000
HAIKU_OUTPUT_PRICE = 0.40 / 1_000_000

LOGO_URL = "https://osonphsavryefwmlhkyv.supabase.co/storage/v1/object/public/assets/lepefy-logo-email.png"

# ─── Supabase ─────────────────────────────────────────────────────────────────

def _supabase_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


def _fetch_top_deals() -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=HOURS_LOOKBACK)).isoformat()
    params = {
        "select": "title,price_value,score,margine_stimato,motivazione,keyword,location,url",
        "created_at": f"gte.{since}",
        "score": f"gte.{MIN_SCORE}",
        "order": "score.desc",
        "limit": str(TOP_N_DEALS),
    }
    with httpx.Client() as client:
        resp = client.get(
            f"{SUPABASE_URL}/rest/v1/scan_results",
            headers=_supabase_headers(),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


def _log_ai_usage(input_tokens: int, output_tokens: int, deals_scored: int) -> None:
    cost = (input_tokens * HAIKU_INPUT_PRICE) + (output_tokens * HAIKU_OUTPUT_PRICE)
    record = {
        "keyword":       "content_generator",
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "cost_usd":      round(cost, 6),
        "deals_scored":  deals_scored,
    }
    try:
        with httpx.Client() as client:
            client.post(
                f"{SUPABASE_URL}/rest/v1/ai_usage_log",
                headers=_supabase_headers(),
                json=record,
                timeout=10,
            )
    except Exception:
        pass  # log failure non blocca il flusso


# ─── Claude ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Sei il social media manager di Lepefy, un tool AI che trova affari su marketplace italiani (Subito, eBay, Facebook, Wallapop).
Il tuo tono è diretto, entusiasta ma credibile. Parli italiano. Non esagerare con gli emoji.
Rispondi SOLO con JSON valido, nessun testo fuori dal JSON."""

USER_PROMPT_TEMPLATE = """Genera le caption social per questo deal trovato da Lepefy:

Titolo annuncio: {title}
Prezzo: €{price}
Margine stimato: €{margine}
Categoria: {keyword}
Motivazione AI: {motivazione}
Luogo: {location}

Crea caption distinte e ottimizzate per:
- Instagram (max 150 parole, storytelling, 10-15 hashtag in fondo)
- TikTok (max 80 parole, hook forte nella prima riga, 5-8 hashtag)
- Facebook (max 120 parole, tono più conversazionale, 3-5 hashtag)

Rispondi con questo JSON esatto:
{{
  "instagram": "...",
  "tiktok": "...",
  "facebook": "..."
}}"""


def _generate_content_for_deal(deal: dict) -> tuple[dict, int, int]:
    """
    Chiama Claude Haiku e ritorna (content, input_tokens, output_tokens).
    """
    prompt = USER_PROMPT_TEMPLATE.format(
        title=deal.get("title", "N/A"),
        price=deal.get("price_value", "?"),
        margine=deal.get("margine_stimato", "?"),
        keyword=deal.get("keyword", "N/A"),
        motivazione=deal.get("motivazione", "N/A"),
        location=deal.get("location", "Italia"),
    )
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    with httpx.Client() as client:
        resp = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

    input_tokens  = data.get("usage", {}).get("input_tokens", 0)
    output_tokens = data.get("usage", {}).get("output_tokens", 0)

    raw = data["content"][0]["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw), input_tokens, output_tokens


# ─── Email HTML ───────────────────────────────────────────────────────────────

def _platform_badge(platform: str) -> str:
    colors = {"instagram": "#E1306C", "tiktok": "#000000", "facebook": "#1877F2"}
    color = colors.get(platform, "#666")
    return (
        f'<span style="display:inline-block;background:{color};color:white;'
        f'font-size:11px;font-weight:700;padding:2px 8px;border-radius:4px;'
        f'letter-spacing:0.05em;text-transform:uppercase;">{platform}</span>'
    )


def _deal_block_html(deal: dict, content: dict, index: int) -> str:
    title   = deal.get("title", "N/A")
    price   = deal.get("price_value", "?")
    margine = deal.get("margine_stimato", "?")
    score   = deal.get("score", "?")
    url     = deal.get("url", "#")

    platforms_html = ""
    for platform in ["instagram", "tiktok", "facebook"]:
        caption = content.get(platform, "").replace("\n", "<br/>")
        platforms_html += f"""
        <div style="margin-bottom:16px;">
          <div style="margin-bottom:6px;">{_platform_badge(platform)}</div>
          <div style="background:#F9F8F6;border:1px solid #E8E5DF;border-radius:8px;
                      padding:14px 16px;font-size:14px;color:#333;line-height:1.6;
                      white-space:pre-wrap;font-family:Georgia,serif;">
            {caption}
          </div>
        </div>"""

    return f"""
    <div style="margin-bottom:32px;border:1px solid #E8E5DF;border-radius:12px;overflow:hidden;">
      <div style="background:#FFF1EC;padding:16px 20px;border-bottom:1px solid #FFD5C2;">
        <div style="font-size:11px;font-family:monospace;color:#999;margin-bottom:4px;">
          DEAL #{index} &nbsp;·&nbsp; SCORE {score}/10 &nbsp;·&nbsp; MARGINE €{margine}
        </div>
        <div style="font-size:16px;font-weight:700;color:#111;margin-bottom:4px;">{title}</div>
        <div style="font-size:14px;color:#FF4D00;font-weight:600;">€{price}</div>
        <div style="margin-top:8px;">
          <a href="{url}" style="font-size:12px;color:#6366F1;text-decoration:none;">→ Vedi annuncio</a>
        </div>
      </div>
      <div style="padding:20px;">{platforms_html}</div>
    </div>"""


def _build_email_html(deals: list[dict], contents: list[dict]) -> str:
    today     = datetime.now().strftime("%d %B %Y")
    deals_html = "".join(
        _deal_block_html(deal, content, i + 1)
        for i, (deal, content) in enumerate(zip(deals, contents))
    )
    return f"""<!DOCTYPE html>
<html lang="it">
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#F9F8F6;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;">
  <div style="max-width:620px;margin:0 auto;padding:32px 16px;">
    <div style="text-align:center;margin-bottom:32px;">
      <img src="{LOGO_URL}" alt="Lepefy" height="36" style="height:36px;"/>
    </div>
    <div style="background:#111110;border-radius:12px;padding:28px 24px;margin-bottom:28px;text-align:center;">
      <div style="font-size:11px;font-family:monospace;color:#666;letter-spacing:0.12em;text-transform:uppercase;margin-bottom:8px;">Contenuti Social · {today}</div>
      <div style="font-size:22px;font-weight:800;color:white;letter-spacing:-0.03em;">{len(deals)} deal pronti da pubblicare</div>
      <div style="font-size:14px;color:rgba(255,255,255,0.4);margin-top:6px;">Caption generate da AI per Instagram, TikTok e Facebook</div>
    </div>
    {deals_html}
    <div style="background:#F0F0EE;border-radius:8px;padding:16px 20px;margin-top:8px;">
      <div style="font-size:12px;color:#888;line-height:1.6;">
        💡 <strong>Tip:</strong> posta su TikTok prima, poi condividi su Instagram Reels e Facebook.
        Il formato video con screen recording dell'annuncio converte meglio del solo testo.
      </div>
    </div>
    <div style="text-align:center;margin-top:28px;">
      <div style="font-size:11px;font-family:monospace;color:#CCC;letter-spacing:0.06em;">LEPEFY · CONTENUTI AUTOMATICI · {today}</div>
    </div>
  </div>
</body>
</html>"""


# ─── Brevo ────────────────────────────────────────────────────────────────────

def _send_email(html: str, n_deals: int) -> bool:
    today = datetime.now().strftime("%d/%m/%Y")
    payload = {
        "sender":      {"email": EMAIL_FROM, "name": EMAIL_FROM_NAME},
        "to":          [{"email": CONTENT_EMAIL_TO}],
        "subject":     f"📱 Lepefy · {n_deals} contenuti social pronti — {today}",
        "htmlContent": html,
    }
    with httpx.Client() as client:
        resp = client.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        return resp.status_code == 201


# ─── Entry point ──────────────────────────────────────────────────────────────

def run_content_job() -> dict:
    # 1. Fetch deal
    deals = _fetch_top_deals()
    if not deals:
        return {"status": "skip", "reason": "Nessun deal con score sufficiente nelle ultime 24h"}

    # 2. Genera contenuti tracciando i token
    contents      = []
    errors        = []
    total_input   = 0
    total_output  = 0

    for deal in deals:
        try:
            content, inp, out = _generate_content_for_deal(deal)
            contents.append(content)
            total_input  += inp
            total_output += out
        except Exception as e:
            errors.append(str(e))
            contents.append({"instagram": "Errore generazione", "tiktok": "", "facebook": ""})

    # 3. Log token su ai_usage_log
    _log_ai_usage(total_input, total_output, len(deals))

    # 4. Calcola costo
    cost_usd = round(
        (total_input * HAIKU_INPUT_PRICE) + (total_output * HAIKU_OUTPUT_PRICE), 6
    )

    # 5. Costruisci e invia email
    html = _build_email_html(deals, contents)
    sent = _send_email(html, len(deals))

    return {
        "status":        "ok" if sent else "email_error",
        "deals":         len(deals),
        "errors":        errors,
        "sent":          sent,
        "input_tokens":  total_input,
        "output_tokens": total_output,
        "cost_usd":      cost_usd,
    }
