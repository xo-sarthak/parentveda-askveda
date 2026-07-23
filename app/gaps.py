"""
Content gaps (Phase 4b) — record what we COULDN'T answer.

Every unanswered question is a piece of content waiting to be written. We log it
to `veda_content_gaps`, where repeats increment `ask_count` — turning failures
into a demand-ranked content to-do list. This is the input side of the content
flywheel (Phase 7): gap logged → drafted in Directus → editor publishes → the
next mother gets a cheap grounded answer.
"""

from app.cache import normalize
from app.db import supabase


def log_gap(question: str, stage_key: str = "") -> None:
    """Record (or increment) an unanswered question. Best-effort — never break
    a user's response because bookkeeping failed."""
    q = (question or "").strip()
    if not q:
        return
    try:
        supabase.rpc(
            "log_veda_gap",
            {"q_norm": normalize(q), "q": q, "stage": stage_key or ""},
        ).execute()
    except Exception as e:  # pragma: no cover - logging is non-critical
        print(f"[gaps.log_gap] non-fatal: {e}")
