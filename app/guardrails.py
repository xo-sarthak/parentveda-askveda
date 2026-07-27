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

# --- Trying-to-conceive red flags -------------------------------------------
# TTC has its own emergencies, and one is genuinely time-critical: a positive
# test plus one-sided or shoulder-tip pain can mean an ectopic pregnancy, which
# needs SAME-DAY care. That gets its own wording — still calm, but explicit about
# today — because "see your doctor sometime" is the wrong advice here.
_ECTOPIC_TERMS = [
    "shoulder tip pain", "shoulder-tip pain", "pain in my shoulder tip",
    "one sided pain", "one-sided pain", "pain on one side",
    "ectopic", "tubal pregnancy",
]
# Only urgent alongside a positive test / early pregnancy — but we also fire on
# the terms above alone, because being wrong in this direction is the safe way.
_ECTOPIC_CONTEXT = [
    "positive test", "positive pregnancy test", "bfp", "just found out",
    "pregnant", "missed period", "test positive", "tested positive",
]
_ECTOPIC_MESSAGE = (
    "Because you've had a positive test, this combination is one doctors like to "
    "check the same day — it can point to a pregnancy settling outside the womb, "
    "which is very treatable when it's caught early. Please contact your doctor or "
    "your nearest emergency department today rather than waiting. I can't assess "
    "this from here, and it's genuinely worth being seen."
)

# OHSS — a real risk during/after IVF stimulation.
_OHSS_TERMS = [
    "ohss", "hyperstimulation", "ovarian hyperstimulation",
    "bloated after egg collection", "bloating after ivf", "bloated after ivf",
    "breathless after ivf", "sudden weight gain after ivf",
]
_OHSS_MESSAGE = (
    "Symptoms like rapid bloating, breathlessness or a sudden jump in weight after "
    "a stimulation cycle are ones your fertility clinic asks to hear about "
    "straight away. Please call the clinic that's treating you today — they'll know "
    "your cycle and can check you properly."
)

# Other TTC concerns that deserve a calm doctor nudge (not same-day framing).
_TTC_TERMS = [
    "severe pelvic pain", "severe period pain", "pain during sex",
    "bleeding between periods", "spotting between periods",
    "periods have stopped", "periods stopped", "no period for months",
    "haven't had a period", "havent had a period",
]

# Checked in order — most time-critical first.
_RULES: list[tuple[list[str], str]] = [
    (_OHSS_TERMS, _OHSS_MESSAGE),
    (_TTC_TERMS, _RED_FLAG_MESSAGE),
    (_RED_FLAG_TERMS, _RED_FLAG_MESSAGE),
]


def red_flag_response(question: str) -> str | None:
    """Return a calm doctor-routing message if the question looks urgent, else None."""
    q = (question or "").lower()

    # Ectopic first: strongest signal, and the one that must not be missed.
    if any(t in q for t in _ECTOPIC_TERMS):
        # A positive test alongside it makes it unmistakable, but the pain terms
        # alone are enough — a false positive here only adds a gentle nudge.
        return _ECTOPIC_MESSAGE
    if any(c in q for c in _ECTOPIC_CONTEXT) and any(
        t in q for t in ("bleeding", "dizzy", "dizziness", "faint", "sharp pain")
    ):
        return _ECTOPIC_MESSAGE

    for terms, message in _RULES:
        if any(t in q for t in terms):
            return message
    return None


def within_rate_limit(user_key: str) -> bool:
    """True if this user is still under today's question limit."""
    return count_today(user_key) < settings.rate_limit_per_day


def within_spend_cap() -> bool:
    """True if today's global spend is still under the cap (circuit breaker)."""
    return spend_today() < settings.daily_spend_cap_usd
