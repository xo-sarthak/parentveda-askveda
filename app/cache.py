"""
Cache (Phase 4) — remember answers so repeats cost ~₹0.

Two hit types:
  • EXACT    — identical normalized text + same week_key → a plain table lookup.
  • SEMANTIC — a differently-worded but near-identical question (embedding within
               the threshold, same week_key) → the match_veda_cache RPC.
A hit skips BOTH retrieval and the LLM.

Safety rules baked in:
  • week_key is part of the key, so week-sensitive answers never cross-contaminate.
  • "can I…?" / dosage questions are EXACT-ONLY (never served a merely-similar
    answer), because a subtle wording change there can flip the correct answer.
"""

import json
import re
from datetime import datetime, timezone

from app.config import settings
from app.db import supabase


def _empty_payload(answer: str = "") -> dict:
    return {"answer": answer, "meaning": "", "actions": [],
            "content": [], "videos": [], "products": [], "services": []}


def _load_payload(stored: str) -> dict:
    """The cache column now holds the full structured response as JSON. Old rows may
    still be plain answer text — treat those as answer-only (no sections)."""
    try:
        obj = json.loads(stored)
        if isinstance(obj, dict) and "answer" in obj:
            return {**_empty_payload(), **obj}
    except Exception:
        pass
    return _empty_payload(stored)


def normalize(question: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace → the exact-match key."""
    q = (question or "").strip().lower()
    q = re.sub(r"[^\w\s]", "", q)   # drop punctuation
    q = re.sub(r"\s+", " ", q)      # collapse runs of spaces
    return q.strip()


def stage_key_for(
    week: int | None = None,
    trimester: str | None = None,
    child_age_months: int | None = None,
    stage: str | None = None,
    chapter: str | None = None,
    ttc_path: str | None = None,
    timing_ownership: str | None = None,
    treatment_step: str | None = None,
) -> str:
    """Bucket the asker by LIFE STAGE so cached answers never cross stages.

    Same question + same stage = same correct answer, shared by everyone at that
    stage (cheap). Different stage = a different cache entry (correct). E.g.
    "can we have sex during pregnancy?" is a different answer in T1 vs T3, and
    a mother whose baby is 3 months old is somewhere else entirely.

    TRYING TO CONCEIVE buckets on chapter + path + timing ownership — deliberately
    NOT cycle day. Cycle day would split every question 28 ways and destroy the
    hit rate, while chapter ("the waiting days") and path (natural vs IVF) are
    what actually change the answer's register.

    OWNERSHIP is in the key because it is not implied by the path: the same
    `ivf` can be a natural-cycle transfer (her LH still matters) or a fully
    medicated one (her signals are noise, and the wait ends in a beta test).
    Sharing one cached answer between those two would tell one of them something
    untrue about her own body.

    TREATMENT STEP is in the key for the same reason, more sharply: the wait
    ("nobody knows yet") and a positive result ("an early pregnancy") give
    OPPOSITE answers to the same question, so they must never share an entry.
    It has at most ten values and is only sent while a clinic owns the cycle,
    so the split is small. The day count inside a step is NOT sent, like the
    cycle day and for the same reason.
    """
    if (stage or "").lower() in ("trying", "ttc", "trying_to_conceive"):
        # "s1" = scoped to the stage (2026-09-30, app/answer.py scope_domain).
        # Answers cached before the scope pointed at pregnancy content too; a
        # new key means none of them is served again. Kept for revert: the
        # key without ":s1".
        return (f"ttc:s1:{chapter or '-'}:{ttc_path or '-'}:{timing_ownership or '-'}"
                f":{treatment_step or '-'}")
    if child_age_months is not None:
        return f"cm{child_age_months}"  # parenting: child age in months
    # "s1" on a pregnancy key = scoped to the stage (2026-10-02, app/answer.py
    # scope_domain), exactly as TTC's. Answers cached before the scope could
    # point at another stage's content, or at none of the app's own pages; a new
    # key means none of them is served again. Only when the app SAYS it is the
    # pregnancy side (`stage="pregnancy"`): an older build that sends only a week
    # keeps its old key and its old, unscoped behaviour. Kept for revert: the
    # keys without ":s1".
    scoped = (stage or "").lower() == "pregnancy"
    if week:
        return f"pw{week}" + (":s1" if scoped else "")      # pregnancy: by week
    if trimester:
        return f"pt{trimester}" + (":s1" if scoped else "")  # by trimester (week unknown)
    return "p:s1" if scoped else ""


# Wording changes here can flip the right answer → only reuse an EXACT repeat.
_EXACT_ONLY_HINTS = [
    "can i", "is it safe", "safe to", "safe during", "allowed to",
    "dose", "dosage", "how much", "how many", "mg", "ml", "tablet", "tablets",
]


def _is_exact_only(question: str) -> bool:
    q = question.lower()
    return any(h in q for h in _EXACT_ONLY_HINTS)


# Questions about HER OWN data (her report, her weight, her scan…). The answer
# belongs to one mother only, so it must NEVER be cached and served to another —
# both wrong and a privacy smell. These always go fresh.
_PERSONAL_DATA_HINTS = [
    "my report", "my scan", "my ultrasound", "my result", "my test",
    "my blood", "my bp", "my blood pressure", "my sugar", "my hb",
    "my haemoglobin", "my hemoglobin", "my weight", "my reading",
    "my measurement", "my symptom", "my medicine", "my medication",
    "my baby's weight", "my baby's growth", "my prescription",
]


def is_personal(question: str) -> bool:
    """True if the question depends on THIS mother's own data → never cache it."""
    q = question.lower()
    return any(h in q for h in _PERSONAL_DATA_HINTS)


def _to_vector_literal(vec: list[float]) -> str:
    """pgvector text form for INSERT into the vector column (same as ingest)."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def lookup(question: str, week_key: str, q_vector: list[float]) -> dict | None:
    """Return a cached answer (exact, then semantic) or None on a miss."""
    norm = normalize(question)

    # 1) EXACT — plain lookup on the (question_norm, week_key) unique key.
    res = (
        supabase.table("veda_cache")
        .select("id, answer, cached_question, hit_count")
        .eq("question_norm", norm)
        .eq("week_key", week_key)
        .limit(1)
        .execute()
    )
    if res.data:
        row = res.data[0]
        _touch(row["id"], row.get("hit_count"))
        return {"payload": _load_payload(row["answer"]), "match": "exact"}

    # 2) SEMANTIC — skip for verdict/dosage questions (exact-only).
    if _is_exact_only(question):
        return None

    rpc = supabase.rpc(
        "match_veda_cache",
        {
            "query_embedding": q_vector,
            "week_key_in": week_key,
            "match_count": 1,
            "min_similarity": settings.cache_similarity_threshold,
        },
    ).execute()
    if rpc.data:
        row = rpc.data[0]
        _touch(row["id"], None)
        return {"payload": _load_payload(row["answer"]), "match": "semantic",
                "similarity": row.get("similarity")}

    return None


def store(question: str, week_key: str, q_vector: list[float], payload: dict) -> None:
    """Cache the full structured response (as JSON) so future repeats are free.
    Best-effort — a caching failure must never break the answer."""
    try:
        supabase.table("veda_cache").upsert(
            {
                "question_norm": normalize(question),
                "cached_question": question,
                "question_embedding": _to_vector_literal(q_vector),
                "week_key": week_key,
                "answer": json.dumps(payload, ensure_ascii=False),
            },
            on_conflict="question_norm,week_key",
        ).execute()
    except Exception as e:  # pragma: no cover - caching is non-critical
        print(f"[cache.store] non-fatal: {e}")


def _touch(cache_id: str, current_hits: int | None) -> None:
    """Bump hit_count + last_used on a hit (surfaces FAQs; supports expiry). Best-effort."""
    try:
        if current_hits is None:
            r = (
                supabase.table("veda_cache")
                .select("hit_count")
                .eq("id", cache_id)
                .limit(1)
                .execute()
            )
            current_hits = r.data[0]["hit_count"] if r.data else 0
        supabase.table("veda_cache").update(
            {"hit_count": (current_hits or 0) + 1, "last_used": _now_iso()}
        ).eq("id", cache_id).execute()
    except Exception as e:  # pragma: no cover
        print(f"[cache._touch] non-fatal: {e}")
