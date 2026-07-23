"""
Guardrails (Phase 4) — the safety + abuse rails that run BEFORE we spend an LLM call.

Four checks, cheapest/most-important first:
  1. red-flag routing — possible emergency → calm doctor note, skip RAG entirely.
  2. rate limit       — 20 questions/user/day (anti-spam, cost control).
  3. spend cap        — global daily circuit-breaker (cannot-explode insurance).
Scope ("is this even pregnancy/parenting?") is handled downstream by the retrieval
confidence floor + the grounded prompt, so a bad question fails cheaply.
"""

from app.config import settings
from app.usage import count_today, spend_today

# Phrases that suggest something needing prompt, in-person care. Deliberately
# broad — a false positive just adds a gentle "please see your doctor" nudge,
# which is a safe way to be wrong for a health assistant.
_RED_FLAG_TERMS = [
    "heavy bleeding", "bleeding a lot", "lot of blood", "gushing", "hemorrhage",
    "severe pain", "unbearable pain", "sharp pain", "severe cramp",
    "baby not moving", "not moving", "no movement", "reduced movement",
    "less movement", "hasn't moved", "stopped moving", "isn't kicking",
    "water broke", "water breaking", "fluid leaking", "my water",
    "blurred vision", "blurry vision", "severe headache",
    "fainted", "passed out", "can't breathe", "trouble breathing",
    "chest pain", "high fever", "seizure", "convulsion",
    "want to die", "kill myself", "suicidal", "end my life", "harm myself",
]

# Calm, no alarm styling (per the product decision).
_RED_FLAG_MESSAGE = (
    "This sounds like something that may need prompt, in-person care. Please reach "
    "out to your doctor or midwife right away — or your nearest clinic — so someone "
    "can help you directly and safely. I can share general information, but I'm not "
    "able to assess something like this."
)


def red_flag_response(question: str) -> str | None:
    """Return a calm doctor-routing message if the question looks urgent, else None."""
    q = question.lower()
    if any(term in q for term in _RED_FLAG_TERMS):
        return _RED_FLAG_MESSAGE
    return None


def within_rate_limit(user_key: str) -> bool:
    """True if this user is still under today's question limit."""
    return count_today(user_key) < settings.rate_limit_per_day


def within_spend_cap() -> bool:
    """True if today's global spend is still under the cap (circuit breaker)."""
    return spend_today() < settings.daily_spend_cap_usd
