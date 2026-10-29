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
    pregnancy_ended: bool | None = None,
    family_stage: str | None = None,
    previous_loss: bool | None = None,
    corrected_months: int | None = None,
    signals: list[str] | None = None,
    conditions: list[str] | None = None,
    window_source: str | None = None,
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
    # After a loss, a new chapter (2026-10-26): her answers are framed with no
    # congratulations and room for worry, so they never share an entry with
    # the same stage's ordinary answers. ":al" = after a loss.
    al = (":al" if previous_loss and not pregnancy_ended
          and child_age_months is None else "")
    if (stage or "").lower() in ("trying", "ttc", "trying_to_conceive"):
        # "s1" = scoped to the stage (2026-09-30, app/answer.py scope_domain).
        # Answers cached before the scope pointed at pregnancy content too; a
        # new key means none of them is served again. Kept for revert: the
        # key without ":s1".
        # WINDOW SOURCE (2026-10-29, app T2): "your days follow your positive
        # test" must never be served to someone with no test. Two values, sent
        # only when a signal was logged, and appended only then, so everyone
        # else keeps the old key and its cached answers. It arrives cleaned
        # (prompt.clean_window_source). Kept for revert: the return without
        # the suffix.
        ws = f":w{window_source}" if window_source else ""
        return (f"ttc:s1:{chapter or '-'}:{ttc_path or '-'}:{timing_ownership or '-'}"
                f":{treatment_step or '-'}{al}{ws}")
    if (stage or "").lower() == "parenting":
        # "s1" = scoped to the stage (2026-10-10), as TTC and pregnancy. Kept
        # for revert: the key without ":s1".
        base = (f"cm{child_age_months}" if child_age_months is not None else "c") + ":s1"
        # F8 (2026-10-25): a mother pregnant again, or trying again, is told
        # something different from one who is neither ("no longer pregnant"
        # would be untrue for her), so her answers never share an entry.
        fam = (family_stage or "").lower()
        if fam == "pregnancy":
            base += ":fp"
        elif fam in ("trying", "ttc", "trying_to_conceive"):
            base += ":ft"
        # The child's context (2026-10-29, app PP 6): every fact that changes
        # the answer is in the key, or a formula baby's answer is served to a
        # breastfed one. The ids arrive cleaned (prompt.clean_ids: a closed,
        # sorted list), so the split is bounded. The cost is hit rate, paid
        # only by families who told us something; a family who told us nothing
        # keeps the old key and its cached answers. Kept for revert: the
        # early returns above with no suffix below.
        if corrected_months is not None:
            base += f":cc{corrected_months}"
        if signals:
            base += ":g=" + "+".join(signals)
        if conditions:
            base += ":k=" + "+".join(conditions)
        return base
    # After a loss (R3, 2026-10-25): one bucket of its own, never a week's. An
    # answer cached for week 14 speaks of a baby growing; it must not be served
    # to a mother whose pregnancy has ended, and hers must not reach week 14.
    if pregnancy_ended and child_age_months is None:
        return "pl:s1"
    if child_age_months is not None:
        return f"cm{child_age_months}"  # parenting: child age in months (older build)
    # "s1" on a pregnancy key = scoped to the stage (2026-10-02, app/answer.py
    # scope_domain), exactly as TTC's. Answers cached before the scope could
    # point at another stage's content, or at none of the app's own pages; a new
    # key means none of them is served again. Only when the app SAYS it is the
    # pregnancy side (`stage="pregnancy"`): an older build that sends only a week
    # keeps its old key and its old, unscoped behaviour. Kept for revert: the
    # keys without ":s1".
    #
    # ":s2" (2026-10-13): the FAQ Library joined pregnancy's knowledge and the
    # prompt gained rule 10 (write for where she is). An answer cached before
    # either was grounded without the FAQs and framed less for her stage; a new
    # key means none of them is served again. The cost, named: every pregnancy
    # question is paid for once more (the user: the AI cost is fine). Kept for
    # revert: ":s1" in place of ":s2".
    scoped = (stage or "").lower() == "pregnancy"
    if week:
        return f"pw{week}" + (":s2" if scoped else "") + al      # pregnancy: by week
    if trimester:
        return f"pt{trimester}" + (":s2" if scoped else "") + al  # by trimester (week unknown)
    return ("p:s2" if scoped else "") + al


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
