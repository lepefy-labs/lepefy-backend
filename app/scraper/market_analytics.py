"""
market_analytics.py — Lepefy
Funzioni di analisi sul mercato dell'usato italiano.
Legge da market_snapshots, non scrive mai.

Usare le funzioni di questo modulo dagli endpoint di main.py.
"""

import os
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

TABLE = "market_snapshots"


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. Statistiche di prezzo
# ---------------------------------------------------------------------------

def get_price_stats(
    modello: str,
    condizione: str | None = None,
    giorni: int = 90,
) -> dict:
    """
    Ritorna statistiche di prezzo per un modello nell'ultimo periodo.

    Args:
        modello:    es. "A7 III"
        condizione: es. "Ottime condizioni" — se None, include tutte
        giorni:     finestra temporale in giorni (default 90)

    Returns:
        {
            "modello": str,
            "condizione": str | None,
            "giorni": int,
            "count": int,
            "min": float | None,
            "max": float | None,
            "media": float | None,
            "mediana": float | None,
            "p25": float | None,
            "p75": float | None,
        }
    """
    supabase = _get_supabase()
    since = (_now() - timedelta(days=giorni)).isoformat()

    query = (
        supabase.table(TABLE)
        .select("price_value")
        .eq("modello", modello)
        .gte("first_seen_at", since)
        .not_.is_("price_value", "null")
    )
    if condizione:
        query = query.eq("condizione", condizione)

    response = query.execute()
    prices = sorted([r["price_value"] for r in (response.data or []) if r["price_value"]])

    if not prices:
        return {
            "modello": modello, "condizione": condizione, "giorni": giorni,
            "count": 0, "min": None, "max": None,
            "media": None, "mediana": None, "p25": None, "p75": None,
        }

    count = len(prices)
    media = round(sum(prices) / count, 2)
    mediana = _percentile(prices, 50)
    p25 = _percentile(prices, 25)
    p75 = _percentile(prices, 75)

    return {
        "modello":    modello,
        "condizione": condizione,
        "giorni":     giorni,
        "count":      count,
        "min":        round(prices[0], 2),
        "max":        round(prices[-1], 2),
        "media":      media,
        "mediana":    mediana,
        "p25":        p25,
        "p75":        p75,
    }


def _percentile(sorted_values: list[float], pct: int) -> float:
    """Calcola un percentile da una lista già ordinata."""
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct / 100
    f, c = int(k), min(int(k) + 1, len(sorted_values) - 1)
    return round(sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f), 2)


# ---------------------------------------------------------------------------
# 2. Time-to-sell
# ---------------------------------------------------------------------------

def get_time_to_sell(
    modello: str,
    condizione: str | None = None,
    giorni: int = 180,
) -> dict:
    """
    Calcola il tempo medio di permanenza sul mercato per gli annunci venduti.

    Args:
        modello:    es. "A7 III"
        condizione: filtra per condizione se specificata
        giorni:     considera solo annunci venduti negli ultimi N giorni

    Returns:
        {
            "modello": str,
            "condizione": str | None,
            "count_venduti": int,
            "giorni_medi": float | None,
            "giorni_mediana": float | None,
            "giorni_min": float | None,
            "giorni_max": float | None,
        }
    """
    supabase = _get_supabase()
    since = (_now() - timedelta(days=giorni)).isoformat()

    query = (
        supabase.table(TABLE)
        .select("first_seen_at, sold_at")
        .eq("modello", modello)
        .eq("is_sold", True)
        .gte("sold_at", since)
        .not_.is_("sold_at", "null")
    )
    if condizione:
        query = query.eq("condizione", condizione)

    response = query.execute()
    rows = response.data or []

    durate = []
    for r in rows:
        try:
            first = datetime.fromisoformat(r["first_seen_at"])
            sold = datetime.fromisoformat(r["sold_at"])
            delta = (sold - first).total_seconds() / 86400  # giorni
            if delta >= 0:
                durate.append(delta)
        except Exception:
            continue

    durate.sort()

    if not durate:
        return {
            "modello": modello, "condizione": condizione,
            "count_venduti": 0,
            "giorni_medi": None, "giorni_mediana": None,
            "giorni_min": None, "giorni_max": None,
        }

    return {
        "modello":        modello,
        "condizione":     condizione,
        "count_venduti":  len(durate),
        "giorni_medi":    round(sum(durate) / len(durate), 1),
        "giorni_mediana": round(_percentile(durate, 50), 1),
        "giorni_min":     round(durate[0], 1),
        "giorni_max":     round(durate[-1], 1),
    }


# ---------------------------------------------------------------------------
# 3. Trend prezzi nel tempo
# ---------------------------------------------------------------------------

def get_price_trend(
    modello: str,
    giorni: int = 90,
    bucket: str = "week",
) -> dict:
    """
    Ritorna l'andamento del prezzo mediano nel tempo, raggruppato per bucket.

    Args:
        modello: es. "A7 III"
        giorni:  finestra temporale (default 90)
        bucket:  "day" | "week" | "month"

    Returns:
        {
            "modello": str,
            "bucket": str,
            "punti": [{"periodo": str, "mediana": float, "count": int}, ...]
        }
    """
    supabase = _get_supabase()
    since = (_now() - timedelta(days=giorni)).isoformat()

    response = (
        supabase.table(TABLE)
        .select("price_value, first_seen_at")
        .eq("modello", modello)
        .gte("first_seen_at", since)
        .not_.is_("price_value", "null")
        .order("first_seen_at")
        .execute()
    )
    rows = response.data or []

    # Raggruppa per bucket
    buckets: dict[str, list[float]] = {}
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["first_seen_at"])
            key = _bucket_key(dt, bucket)
            buckets.setdefault(key, []).append(r["price_value"])
        except Exception:
            continue

    punti = [
        {
            "periodo": key,
            "mediana": _percentile(sorted(vals), 50),
            "count":   len(vals),
        }
        for key, vals in sorted(buckets.items())
    ]

    return {"modello": modello, "bucket": bucket, "punti": punti}


def _bucket_key(dt: datetime, bucket: str) -> str:
    if bucket == "day":
        return dt.strftime("%Y-%m-%d")
    if bucket == "month":
        return dt.strftime("%Y-%m")
    # default: week (lunedì della settimana)
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 4. Annunci attivi
# ---------------------------------------------------------------------------

def get_active_listings(
    categoria: str | None = None,
    marca: str | None = None,
    modello: str | None = None,
    condizione: str | None = None,
    limit: int = 50,
) -> dict:
    """
    Ritorna gli annunci attualmente attivi (is_sold = false).
    Tutti i parametri sono opzionali e combinabili.

    Returns:
        {
            "count": int,
            "listings": [
                {
                    "keyword": str,
                    "categoria": str,
                    "marca": str,
                    "modello": str,
                    "price_value": float,
                    "location": str,
                    "condizione": str,
                    "first_seen_at": str,
                    "last_seen_at": str,
                },
                ...
            ]
        }
    """
    supabase = _get_supabase()

    query = (
        supabase.table(TABLE)
        .select(
            "keyword, categoria, marca, modello, "
            "price_value, location, condizione, "
            "first_seen_at, last_seen_at"
        )
        .eq("is_sold", False)
        .not_.is_("price_value", "null")
        .order("first_seen_at", desc=True)
        .limit(limit)
    )

    if categoria:
        query = query.eq("categoria", categoria)
    if marca:
        query = query.eq("marca", marca)
    if modello:
        query = query.eq("modello", modello)
    if condizione:
        query = query.eq("condizione", condizione)

    response = query.execute()
    listings = response.data or []

    return {"count": len(listings), "listings": listings}
