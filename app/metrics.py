"""
Observability (Phase 8) — turn the raw logs into the numbers that matter.

Reads `veda_usage_log`, `veda_content_gaps` and `veda_cache` and reports:
  • cache-hit rate      — the single biggest cost lever
  • cost per answer      — is the model choice holding up?
  • web-fallback rate    — how often our content falls short
  • today's spend        — vs the daily cap
  • top gaps             — what to write next (demand-ranked)
  • top cached questions — the real FAQs

Volumes are small, so we sum in Python. At scale these become SQL aggregates.
"""

from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db import supabase


def _since_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _start_of_utc_day_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def summary(days: int = 7) -> dict:
    rows = (
        supabase.table("veda_usage_log")
        .select("cache_hit, used_web, cost_usd, input_tokens, output_tokens, created_at")
        .gte("created_at", _since_iso(days))
        .execute()
    ).data or []

    n = len(rows)
    cache_hits = sum(1 for r in rows if r.get("cache_hit"))
    web = sum(1 for r in rows if r.get("used_web"))
    cost = sum(float(r.get("cost_usd") or 0) for r in rows)

    today = [r for r in rows if r.get("created_at", "") >= _start_of_utc_day_iso()]
    spend_today = sum(float(r.get("cost_usd") or 0) for r in today)

    def rate(x: int) -> float:
        return round(x / n, 3) if n else 0.0

    return {
        "window_days": days,
        "answers": n,
        "cache_hit_rate": rate(cache_hits),          # higher = cheaper
        "web_fallback_rate": rate(web),              # higher = more content gaps
        "llm_answers": n - cache_hits,               # the ones that actually cost
        "total_cost_usd": round(cost, 6),
        "avg_cost_per_answer_usd": round(cost / n, 6) if n else 0.0,
        "spend_today_usd": round(spend_today, 6),
        "daily_spend_cap_usd": settings.daily_spend_cap_usd,
        "answers_today": len(today),
    }


def top_gaps(limit: int = 10) -> list[dict]:
    """Most-asked questions we couldn't answer — the content to-do list."""
    return (
        supabase.table("veda_content_gaps")
        .select("question, ask_count, stage_key, status, last_asked")
        .eq("status", "open")
        .order("ask_count", desc=True)
        .limit(limit)
        .execute()
    ).data or []


def top_cached(limit: int = 10) -> list[dict]:
    """Most-reused answers — the real FAQs (and where caching pays off most)."""
    return (
        supabase.table("veda_cache")
        .select("cached_question, hit_count, week_key")
        .order("hit_count", desc=True)
        .limit(limit)
        .execute()
    ).data or []


def full_report(days: int = 7) -> dict:
    return {
        "summary": summary(days),
        "top_gaps": top_gaps(),
        "top_cached": top_cached(),
    }
