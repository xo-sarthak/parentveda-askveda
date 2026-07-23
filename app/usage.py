"""
Usage log + counters (Phase 4) — the honest money meter.

Every answered question writes one row to `veda_usage_log`. Those rows power three
things: the per-user RATE LIMIT (count a user's rows today), the global DAILY SPEND
CAP (sum today's cost), and later METRICS (cache-hit rate, cost/answer). Cost is
measured from day one instead of guessed.
"""

from datetime import datetime, timezone

from app.db import supabase


def _start_of_utc_day_iso() -> str:
    """Midnight UTC today, as an ISO string — the lower bound for "today"."""
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat()


def count_today(user_key: str) -> int:
    """How many questions this user has asked since midnight UTC (rate limiting)."""
    res = (
        supabase.table("veda_usage_log")
        .select("id", count="exact")
        .eq("user_key", user_key)
        .gte("created_at", _start_of_utc_day_iso())
        .execute()
    )
    return res.count or 0


def spend_today() -> float:
    """Total cost logged across ALL users since midnight UTC (spend cap)."""
    res = (
        supabase.table("veda_usage_log")
        .select("cost_usd")
        .gte("created_at", _start_of_utc_day_iso())
        .execute()
    )
    return sum(float(r["cost_usd"]) for r in (res.data or []))


def log_usage(
    *,
    channel: str,
    user_key: str,
    cache_hit: bool = False,
    used_web: bool = False,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
) -> None:
    """Record one answered question. Best-effort: a logging failure must never
    break a user's answer, so we swallow errors here."""
    try:
        supabase.table("veda_usage_log").insert({
            "channel": channel,
            "user_key": user_key,
            "cache_hit": cache_hit,
            "used_web": used_web,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
        }).execute()
    except Exception as e:  # pragma: no cover - logging is non-critical
        print(f"[usage.log_usage] non-fatal: {e}")
