# app/routers/app_api.py
"""
Endpoints dedicati alla PWA Lepefy.
Autenticazione via Supabase JWT (header: Authorization: Bearer <token>)
Query parallele con asyncio.gather per minimizzare latenza.
"""

import os
import asyncio
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from supabase import create_client, Client

router = APIRouter(prefix="/app", tags=["app"])

SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

FREE_WEEKLY_LIMIT   = 3
DEFECTIVE_CONDITION = "Non del tutto funzionante"

DEAL_SELECT = (
    "id, title, price_value, margine_stimato, score, source, "
    "location, condition, image_url, url, keyword, created_at, body"
)


def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ─── AUTH HELPER ─────────────────────────────────────────────────────────────

async def get_current_user(authorization: str = Header(...)) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token mancante")
    token = authorization.split(" ", 1)[1]
    try:
        supabase = get_supabase()
        user = supabase.auth.get_user(token)
        if not user or not user.user:
            raise HTTPException(status_code=401, detail="Token non valido")
        return {"id": user.user.id, "email": user.user.email}
    except Exception:
        raise HTTPException(status_code=401, detail="Autenticazione fallita")


# ─── FEED ────────────────────────────────────────────────────────────────────

def _query_subito(supabase: Client, keywords: list[str], min_score: int, limit: int) -> list[dict]:
    return (
        supabase.table("scan_results")
        .select(DEAL_SELECT)
        .eq("scored", True)
        .not_.is_("score", "null")
        .gte("score", min_score)
        .in_("keyword", keywords)
        .eq("source", "Subito.it")
        .eq("is_sold", False)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    ).data or []


def _query_vinted_flipper(supabase: Client, keywords: list[str], min_score: int, limit: int) -> list[dict]:
    return (
        supabase.table("scan_results")
        .select(DEAL_SELECT)
        .eq("scored", True)
        .not_.is_("score", "null")
        .gte("score", min_score)
        .in_("keyword", keywords)
        .eq("source", "Vinted.it")
        .eq("is_sold", False)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    ).data or []


def _query_defective(supabase: Client, keywords: list[str]) -> list[dict]:
    return (
        supabase.table("scan_results")
        .select(DEAL_SELECT)
        .eq("scored", True)
        .is_("score", "null")
        .eq("source", "Vinted.it")
        .eq("condition", DEFECTIVE_CONDITION)
        .eq("is_sold", False)
        .in_("keyword", keywords)
        .order("price_value", desc=False)
        .limit(10)
        .execute()
    ).data or []


def _query_collector_sub(supabase: Client, sub: dict) -> list[dict]:
    query = (
        supabase.table("scan_results")
        .select(DEAL_SELECT)
        .ilike("keyword", sub["keyword"])
        .gte("price_value", sub.get("min_threshold") or 0)
        .lte("price_value", sub.get("max_threshold") or 999999)
        .eq("is_sold", False)
        .order("price_value", desc=False)
        .limit(10)
    )
    if sub.get("source"):
        query = query.eq("source", sub["source"])
    return query.execute().data or []


def _interleave(a: list, b: list) -> list:
    """Alterna elementi di due liste: [a0, b0, a1, b1, ...]"""
    result = []
    for i in range(max(len(a), len(b))):
        if i < len(a): result.append(a[i])
        if i < len(b): result.append(b[i])
    return result


def _add_margin(deals: list[dict]) -> list[dict]:
    for d in deals:
        price = d.get("price_value") or 0
        mkt   = d.get("margine_stimato") or 0
        if price and mkt and d.get("score") is not None:
            d["margin"]     = round(mkt - price, 2)
            d["margin_pct"] = round(((mkt - price) / mkt) * 100, 1) if mkt else 0
        else:
            d["margin"]     = None
            d["margin_pct"] = None
    return deals


@router.get("/feed")
async def get_feed(
    platform: Optional[str] = None,
    min_score: Optional[int] = None,
    limit: int = 30,
    user: dict = Depends(get_current_user),
):
    """
    Feed deal con query parallele per Subito, Vinted, difettosi e collezionisti.
    I deal flipper vengono interleaved (alternati Subito/Vinted) prima di essere
    restituiti, garantendo visibilità bilanciata delle due piattaforme.
    """
    supabase = get_supabase()

    # ── Subscription utente ───────────────────────────────────────────────────
    all_subs = (
        supabase.table("subscriptions")
        .select("keyword, min_threshold, max_threshold, plan, is_collector, include_defective, source")
        .eq("email", user["email"])
        .eq("active", True)
        .execute()
    ).data or []

    if not all_subs:
        return {"deals": [], "plan": "free", "count": 0}

    # Piano normalizzato
    plan = next(
        (s.get("plan") for s in all_subs if s.get("plan") and s.get("plan", "").lower() != "free"),
        all_subs[0].get("plan", "free")
    )
    plan = (plan or "free").lower()

    # Segmenta subscription per tipo
    flipper_keywords   = [s["keyword"] for s in all_subs if not s.get("is_collector")]
    defective_subs     = [s for s in all_subs if s.get("include_defective")]
    defective_keywords = [s["keyword"] for s in defective_subs]
    collector_subs     = [s for s in all_subs if s.get("is_collector")]

    effective_min_score = min_score or 7

    # ── Query parallele ───────────────────────────────────────────────────────
    tasks = []

    # Task 0 — Subito flipper
    if flipper_keywords and platform != "Vinted.it":
        tasks.append(asyncio.to_thread(_query_subito, supabase, flipper_keywords, effective_min_score, limit))
    else:
        tasks.append(asyncio.sleep(0, result=[]))

    # Task 1 — Vinted flipper
    if flipper_keywords and platform != "Subito.it":
        tasks.append(asyncio.to_thread(_query_vinted_flipper, supabase, flipper_keywords, effective_min_score, limit))
    else:
        tasks.append(asyncio.sleep(0, result=[]))

    # Task 2 — Difettosi
    if defective_keywords:
        tasks.append(asyncio.to_thread(_query_defective, supabase, defective_keywords))
    else:
        tasks.append(asyncio.sleep(0, result=[]))

    # Task 3..N — Collezionisti (una task per subscription)
    collector_tasks_start = 3
    for sub in collector_subs:
        tasks.append(asyncio.to_thread(_query_collector_sub, supabase, sub))

    # Esegui tutto in parallelo
    results = await asyncio.gather(*tasks)

    subito_deals    = results[0] if results[0] else []
    vinted_deals    = results[1] if results[1] else []
    defective_deals = results[2] if results[2] else []

    # Collezionisti — unisci e deduplica
    collector_deals_raw = []
    for r in results[collector_tasks_start:]:
        collector_deals_raw += r if r else []
    seen = set()
    collector_deals = []
    for d in collector_deals_raw:
        if d["id"] not in seen:
            seen.add(d["id"])
            collector_deals.append(d)

    # ── Filtra per piattaforma se richiesto ───────────────────────────────────
    if platform:
        p = platform.lower()
        subito_deals    = [d for d in subito_deals    if p in (d.get("source") or "").lower()]
        vinted_deals    = [d for d in vinted_deals    if p in (d.get("source") or "").lower()]
        collector_deals = [d for d in collector_deals if p in (d.get("source") or "").lower()]

    # ── Interleave Subito + Vinted flipper ────────────────────────────────────
    normal_deals = _interleave(subito_deals, vinted_deals)

    # ── Unisci tutto: flipper bilanciati + collezionista + difettosi ──────────
    all_deals = _add_margin(normal_deals + collector_deals + defective_deals)

    return {"deals": all_deals, "plan": plan, "count": len(all_deals)}


# ─── DEAL DETAIL ─────────────────────────────────────────────────────────────

@router.get("/deal/{deal_id}")
async def get_deal(deal_id: str, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    result = (
        supabase.table("scan_results")
        .select("*")
        .eq("id", deal_id)
        .single()
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Deal non trovato")
    return result.data


# ─── SAVED DEALS ─────────────────────────────────────────────────────────────

class SaveDealRequest(BaseModel):
    scan_result_id: Optional[str] = None
    title: str
    price: float
    market_price: Optional[float] = None
    margin: Optional[float] = None
    margin_pct: Optional[float] = None
    platform: Optional[str] = None
    city: Optional[str] = None
    score: Optional[int] = None
    url: Optional[str] = None


@router.post("/saved")
async def save_deal(body: SaveDealRequest, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    result = supabase.table("saved_deals").insert({"user_id": user["id"], **body.dict()}).execute()
    return {"saved": True, "id": result.data[0]["id"]}


@router.get("/saved")
async def get_saved_deals(user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    deals = (
        supabase.table("saved_deals")
        .select("*")
        .eq("user_id", user["id"])
        .order("saved_at", desc=True)
        .execute()
    ).data or []
    total_real_margin = sum(d["real_margin"] for d in deals if d.get("real_margin"))
    bought_count = sum(1 for d in deals if d.get("bought"))
    return {
        "deals": deals,
        "stats": {
            "total_saved": len(deals),
            "bought": bought_count,
            "total_real_margin": round(total_real_margin, 2),
        }
    }


class OutcomeRequest(BaseModel):
    bought: bool
    bought_at: Optional[float] = None
    sold_at: Optional[float] = None
    outcome_date: Optional[str] = None


@router.patch("/saved/{saved_id}/outcome")
async def update_outcome(saved_id: str, body: OutcomeRequest, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    real_margin = round(body.sold_at - body.bought_at, 2) if body.bought_at and body.sold_at else None
    supabase.table("saved_deals").update({
        "bought": body.bought,
        "bought_at": body.bought_at,
        "sold_at": body.sold_at,
        "real_margin": real_margin,
        "outcome_date": body.outcome_date or str(date.today()),
    }).eq("id", saved_id).eq("user_id", user["id"]).execute()
    return {"updated": True, "real_margin": real_margin}


@router.delete("/saved/{saved_id}")
async def delete_saved(saved_id: str, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    supabase.table("saved_deals").delete().eq("id", saved_id).eq("user_id", user["id"]).execute()
    return {"deleted": True}


# ─── USER ALERTS ─────────────────────────────────────────────────────────────

class AlertRequest(BaseModel):
    keyword: str
    max_price: Optional[float] = None
    min_score: int = 7


@router.get("/alerts")
async def get_alerts(user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    return {"alerts": (
        supabase.table("user_alerts")
        .select("*")
        .eq("user_id", user["id"])
        .order("created_at", desc=True)
        .execute()
    ).data or []}


@router.post("/alerts")
async def create_alert(body: AlertRequest, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    result = supabase.table("user_alerts").insert({
        "user_id":   user["id"],
        "keyword":   body.keyword.lower().strip(),
        "max_price": body.max_price,
        "min_score": body.min_score,
        "active":    True,
    }).execute()
    return {"created": True, "id": result.data[0]["id"]}


@router.patch("/alerts/{alert_id}")
async def toggle_alert(alert_id: str, active: bool, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    supabase.table("user_alerts").update({"active": active}).eq("id", alert_id).eq("user_id", user["id"]).execute()
    return {"updated": True}


@router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: str, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    supabase.table("user_alerts").delete().eq("id", alert_id).eq("user_id", user["id"]).execute()
    return {"deleted": True}


# ─── SUBSCRIPTION / KEYWORD CONFIG ───────────────────────────────────────────

@router.get("/profile")
async def get_profile(user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    subscriptions = (
        supabase.table("subscriptions")
        .select("*")
        .eq("email", user["email"])
        .execute()
    ).data or []
    for s in subscriptions:
        if s.get("plan"):
            s["plan"] = s["plan"].lower()
    return {"subscriptions": subscriptions}


class SubscriptionUpdate(BaseModel):
    keyword: str
    min_threshold: Optional[float] = None
    max_threshold: Optional[float] = None
    active: Optional[bool] = None
    is_collector: Optional[bool] = None
    include_defective: Optional[bool] = None


@router.patch("/profile/subscription")
async def update_subscription(body: SubscriptionUpdate, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    update_data = {k: v for k, v in body.dict().items() if v is not None and k != "keyword"}
    supabase.table("subscriptions").update(update_data).eq("email", user["email"]).eq("keyword", body.keyword).execute()
    return {"updated": True}


@router.delete("/profile/subscription/{keyword}")
async def remove_subscription(keyword: str, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    supabase.table("subscriptions").update({"active": False}).eq("email", user["email"]).eq("keyword", keyword).execute()
    return {"deactivated": True}


# ─── MARKET INTEL ─────────────────────────────────────────────────────────────

@router.get("/market")
async def get_market(keyword: Optional[str] = None, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    query = (
        supabase.table("market_snapshots")
        .select("keyword, categoria, price_value, first_seen_at, is_sold, source")
        .eq("is_sold", False)
        .order("first_seen_at", desc=True)
        .limit(200)
    )
    if keyword:
        query = query.ilike("keyword", f"%{keyword}%")

    rows = query.execute().data or []

    from collections import defaultdict
    groups: dict = defaultdict(list)
    for r in rows:
        if r.get("price_value"):
            groups[r["keyword"]].append(float(r["price_value"]))

    return {"market": [
        {
            "keyword": kw,
            "avg":     round(sum(prices) / len(prices), 0),
            "min":     round(min(prices), 0),
            "max":     round(max(prices), 0),
            "volume":  len(prices),
        }
        for kw, prices in groups.items()
    ]}


# ─── PLAN CHECK (usato dal notifier) ─────────────────────────────────────────

@router.get("/plan/{email}")
async def check_plan(email: str):
    supabase = get_supabase()
    result = (
        supabase.table("subscriptions")
        .select("plan, notifications_count_week, notifications_week_reset")
        .eq("email", email)
        .limit(1)
        .execute()
    )
    if not result.data:
        return {"plan": "free", "can_notify": False}

    row  = result.data[0]
    plan = (row.get("plan") or "free").lower()

    if plan != "free":
        return {"plan": plan, "can_notify": True}

    reset_date = row.get("notifications_week_reset")
    today      = date.today()
    if reset_date:
        reset = date.fromisoformat(reset_date)
        if (today - reset).days >= 7:
            supabase.table("subscriptions").update({
                "notifications_count_week": 0,
                "notifications_week_reset": str(today),
            }).eq("email", email).execute()
            count = 0
        else:
            count = row.get("notifications_count_week", 0)
    else:
        count = row.get("notifications_count_week", 0)

    return {
        "plan":       plan,
        "can_notify": count < FREE_WEEKLY_LIMIT,
        "used":       count,
        "limit":      FREE_WEEKLY_LIMIT,
    }
