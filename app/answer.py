"""
The brain — one function that turns a question into the full 7-SECTION response.

Sections 1–3 (answer / meaning / actions) come from the LLM in one call.
Sections 4/6/7 (content / products / services) + videos are POINTERS, built from
the same semantic search, grouped by kind (see sections.py). Every response always
carries all section keys (possibly empty → the app shows "Coming soon").

"One brain, two doors": the app endpoint and the WhatsApp webhook both call here.
WhatsApp only uses `answer` (it can't render sections).

Order — cheapest/safest first, the paid LLM call last:
  rate limit → spend cap → red-flag → cache → wide retrieve → confidence floor
  → generate (answer+meaning+actions) → build sections → store + log
"""

from app import cache, flywheel, gaps, guardrails, sections, usage, web_fallback
from app.config import settings
from app.embeddings import embed_query
from app.llm import _reason, complete
from app.prompt import (
    NO_ANSWER,
    build_full_messages,
    CHILD_CONDITIONS,
    FAMILY_SIGNALS,
    build_web_full_messages,
    clean_corrected_age,
    clean_ids,
    describe_stage,
    describe_ttc_stage,
    clean_window_source,
    parse_structured,
    with_child_context,
    with_previous_loss,
)
from app.retriever import retrieve

import logging

log = logging.getLogger(__name__)

# Canned, friendly responses for the non-answer paths.
_MSG_EMPTY = "Please type a question and I'll do my best to help."
_MSG_THROTTLED = (
    "You've reached today's question limit. Please come back tomorrow — I'll be here."
)
_MSG_BUSY = (
    "I'm taking a short breather to keep things running smoothly. "
    "Please try again in a little while."
)
# 2026-10-09: the model provider failed (no credit, outage, bad key, timeout).
# Before, the exception became an HTTP 500, which the app reads as "no
# internet" - a wrong message to a parent, and it hid the reads we had found.
# Now it is a normal answer that says only what is true for HER: Veda can't
# write one right now, here is what we have. It never names the provider or
# billing; that detail goes to the log for us.
_MSG_RESTING = (
    "Ask Veda is taking a short break and can't write an answer just now. "
    "Please try again in a little while. Anything below is from our own "
    "library and still applies."
)
_MSG_LOW_CONF = (
    "I don't have solid information on that in my content yet. It's best to check "
    "with your doctor, or try rephrasing your question."
)

# The model writes a prose non-answer instead of the sentinel often enough that we
# can't depend on the sentinel alone. These are the shapes it actually produces.
_DECLINE_VERBS = (
    "doesn't mention", "does not mention", "doesn't cover", "does not cover",
    "doesn't contain", "does not contain", "doesn't provide", "does not provide",
    "doesn't include", "does not include", "doesn't specify", "does not specify",
)
_DECLINE_PHRASES = (
    "i don't have information", "i do not have information",
    "i don't have any information", "i don't have that",
    "couldn't find information", "could not find information",
    "couldn't find any information", "cannot find information",
    "can't find information", "no information on", "no information about",
    "no relevant information", "not in the provided content",
    "isn't in the provided content", "not covered in the content",
)


def _is_no_answer(text: str) -> bool:
    """Did the model signal our material doesn't actually cover this? Several
    detectors, because a small model isn't reliably obedient about the sentinel —
    it invents fresh ways to say "I couldn't find it" and we must catch them all,
    or the trusted-web fallback never fires."""
    t = (text or "").lower()
    if NO_ANSWER.lower() in t:
        return True
    if "content" in t and any(v in t for v in _DECLINE_VERBS):
        return True
    return any(p in t for p in _DECLINE_PHRASES)


def _complete_or_none(messages: list[dict]) -> dict | None:
    """complete(), but a provider failure returns None instead of raising.

    The trade-off: we lose the loud 500 (and its traceback in the host's error
    page), and gain an answer the parent can read plus the pointer sections we
    already built for free. The loudness is kept where it belongs - one log line
    naming the reason, the same words /health/llm reports."""
    try:
        return complete(messages)
    except Exception as e:  # noqa: BLE001 - any provider failure lands calmly
        print(f"[answer] model unavailable ({_reason(e)}): {str(e)[:200]}")
        return None


def _cost_usd(input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = settings.active_prices_per_1m_usd
    return round(
        input_tokens / 1_000_000 * price_in
        + output_tokens / 1_000_000 * price_out,
        6,
    )


def _empty_sections() -> dict:
    return {"content": [], "videos": [], "products": [], "services": []}


def _response(answer: str, source: str, *, meaning: str = "", actions=None,
              secs: dict | None = None, cache_hit: bool = False, **extra) -> dict:
    """Assemble the canonical response shape — ALWAYS all 7 sections present."""
    out = {
        "answer": answer,
        "meaning": meaning,
        "actions": actions or [],
        **(secs or _empty_sections()),
        "source": source,
        "cache_hit": cache_hit,
    }
    out.update(extra)
    return out


def _try_trusted_web(question, *, user_key, channel, stage_key, stage_note,
                     q_vector, personal) -> tuple[dict | None, bool]:
    """Last resort before dead-ending: answer from WHITELISTED health authorities.

    Returns (response_or_None, made_llm_call). Web answers carry empty pointer
    sections (our own content didn't cover it → nothing to point to)."""
    results = web_fallback.search_trusted(question)
    if not results:
        return None, False

    out = _complete_or_none(build_web_full_messages(question, results, stage_note=stage_note))
    if out is None:
        return _response(_MSG_RESTING, "llm_unavailable"), False
    cost = _cost_usd(out["input_tokens"], out["output_tokens"])
    usage.log_usage(channel=channel, user_key=user_key, cache_hit=False, used_web=True,
                    input_tokens=out["input_tokens"], output_tokens=out["output_tokens"],
                    cost_usd=cost)

    parsed = parse_structured(out["text"])
    if _is_no_answer(parsed["answer"]):
        return None, True  # even trusted sources didn't cover it

    payload = {"answer": parsed["answer"], "meaning": parsed["meaning"],
               "actions": parsed["actions"], **_empty_sections()}
    if not personal:
        cache.store(question, stage_key, q_vector, payload)  # self-extinguishing
    if settings.flywheel_enabled:
        flywheel.draft_from_gap(question, parsed["answer"], results, stage_key)

    return _response(parsed["answer"], "web", meaning=parsed["meaning"],
                     actions=parsed["actions"], used_web=True, cost_usd=cost,
                     sources=[r["url"] for r in results]), True


# The corpus's name for each app stage (veda_content_chunks.domain).
_STAGE_DOMAINS = {
    "trying": "trying", "ttc": "trying", "trying_to_conceive": "trying",
    # 2026-10-02: pregnancy's own pass (the user: "the way we did for TTC").
    "pregnancy": "pregnancy",
    # 2026-10-10: parenting's own pass. The app now exports every parenting door
    # page, door, tool and buying guide (doc ids `ppv...`) and its parenting
    # screen sends `stage="parenting"`, so a parenting answer points only at
    # pages that screen can open.
    "parenting": "parenting",
}


def scope_domain(stage: str | None, domain: str | None) -> str | None:
    """The one domain an answer is grounded in and points into.

    ⚠️ THE STAGE SHE IS IN (2026-09-30, the user: "if I'm on the trying to
    conceive side and I click Ask Veda, I can search for anything inside the
    trying to conceive side … the range should be within the side of the app
    that you are in"). Ask Veda is now the stage's search as well as its
    answers, so both the retrieved chunks that ground the answer and the
    content/videos/products it points to come from her stage's content only.

    This REVERSES the Chapter 0 rule ("additive framing, never a filter; a TTC
    user asking about labour still gets a full answer"). The cost, named: a
    trying-to-conceive question about labour now finds no content of ours,
    logs a gap and goes to the trusted-web fallback instead of pregnancy
    reads. The gain: every "open in the app" pointer lands on her own side of
    the app. An explicit `domain` from the caller still wins.

    ⚠️ PREGNANCY'S PASS (2026-10-02): the same rule, for the same reason. A
    pregnancy question is grounded in, and points to, pregnancy content only
    (`veda_knowledge.domain = 'pregnancy'`, which holds the older offline corpus
    and, since this pass, the app's doors, reads and tools: doc ids `pvread_`,
    `pvfaq_`, `pvdoor_`, `pvtool_`). The cost, as for TTC: a pregnancy question
    about newborn feeding or about conceiving now finds nothing of ours, logs a
    gap and goes to the trusted-web fallback. The gain: every "open in the app"
    pointer lands on her own side of the app.

    ⚠️ PARENTING'S PASS (2026-10-10): the same rule again. The cost, named: a
    parenting question about her own pregnancy-era scans finds nothing of ours
    and goes to the trusted-web fallback. Only a build that SAYS it is the
    parenting side is scoped; an older build that sends only a child age keeps
    the old unscoped behaviour.
    """
    if domain:
        return domain
    return _STAGE_DOMAINS.get((stage or "").lower())


# ⚠️ HER STAGE FIRST, THEN THE WHOLE LIBRARY (2026-10-31, the user: "if a user
# isn't trying to conceive but they ask a pregnancy doubt, it shouldn't
# matter"). This SOFTENS scope_domain above rather than reverting it: her own
# stage's content still wins whenever it can carry the answer, so most "open in
# the app" pointers still land on her side. Only when the wider library is a
# clearly better match (or hers matches nothing) does the answer come from
# another stage — instead of skipping our own content and going to the web.
# The cost, named: such an answer's cards point into another stage, which the
# app opens in its reader sheet rather than as that stage's own page.
WIDEN_MARGIN = 0.05  # the wide match must beat hers by this much to replace it


def pick_results(scoped: list[dict], wide: list[dict], *,
                 floor: float, margin: float = WIDEN_MARGIN) -> tuple[list[dict], bool]:
    """Her stage's results, unless the whole library is clearly better.

    Returns (results, widened). Pure, so it is tested without a database.
    """
    s = scoped[0]["similarity"] if scoped else 0.0
    w = wide[0]["similarity"] if wide else 0.0
    if s >= floor and s >= w - margin:
        return scoped, False
    if w >= floor and w > s:
        return wide, True
    return scoped, False


def answer(
    question: str,
    *,
    user_key: str,
    channel: str = "app",
    week: int | None = None,
    trimester: str | None = None,
    child_age_months: int | None = None,
    # Trying-to-conceive context. ADDITIVE FRAMING, never a filter — a TTC user
    # asking about labour still gets a full answer ("one mother, one journey").
    stage: str | None = None,
    chapter: str | None = None,
    cycle_day: int | None = None,
    ttc_path: str | None = None,
    # WHO owns the timing of this cycle — more decisive than ttc_path, because a
    # medicated cycle overrides her body's own signals entirely.
    timing_ownership: str | None = None,
    # Where a clinic round stands today; decides whether "not pregnant" is
    # true, unknown (the wait) or false (a positive result). See prompt.py.
    treatment_step: str | None = None,
    months_trying: int | None = None,
    lang: str | None = None,   # 'en' | 'hi' — picks which bilingual twin is shown
    domain: str | None = None,
    # After a loss (R3): no week is sent, and none may be spoken of.
    pregnancy_ended: bool | None = None,
    # Parenting side, her own stage when it is not parenting (F8).
    family_stage: str | None = None,
    # Pregnant again or trying again after a loss she told the app about
    # (2026-10-26): gentler framing on top of her stage, its own cache entries.
    previous_loss: bool | None = None,
    # The child's context, parenting side (2026-10-29, app PP 6): the age by
    # the due date for a baby born early, and closed lists of ids. Framing and
    # cache, never a filter. Cleaned below; nothing else reaches the prompt.
    child_age_corrected_months: int | None = None,
    family_signals: list[str] | None = None,
    child_conditions: list[str] | None = None,
    # What placed her fertile days this cycle (2026-10-29, app T2): 'lh' or
    # 'temperature', absent for her period dates. Framing and cache, cleaned
    # below against the ownership.
    window_source: str | None = None,
) -> dict:
    """Answer one question end to end → the full 7-section response dict."""
    question = (question or "").strip()
    if not question:
        return _response(_MSG_EMPTY, "empty")

    # 1) Per-user rate limit (don't log rejections — they aren't answers).
    if not guardrails.within_rate_limit(user_key):
        return _response(_MSG_THROTTLED, "rate_limited")

    # 2) Global daily spend cap (circuit breaker).
    if not guardrails.within_spend_cap():
        return _response(_MSG_BUSY, "spend_capped")

    # 3) Red-flag routing — possible emergency, skip RAG, calm doctor note.
    rf = guardrails.red_flag_response(question)
    if rf:
        usage.log_usage(channel=channel, user_key=user_key, cache_hit=False)
        return _response(rf, "red_flag")

    # Her stage's content first (see scope_domain and pick_results).
    # Kept for revert — "her stage's content only":
    # domain = scope_domain(stage, domain)
    explicit_domain = domain
    domain = scope_domain(stage, domain)
    # Widen only a scope that came from her stage; a caller's explicit domain
    # is a real filter and stays one.
    may_widen = domain is not None and not explicit_domain

    corrected = clean_corrected_age(child_age_corrected_months, child_age_months)
    signals = clean_ids(family_signals, FAMILY_SIGNALS) if child_age_months is not None else []
    conditions = clean_ids(child_conditions, CHILD_CONDITIONS) if child_age_months is not None else []

    # Embed ONCE — reused by the cache lookup AND retrieval.
    q_vector = embed_query(question)
    window = (clean_window_source(window_source, timing_ownership)
              if (stage or "").lower() in ("trying", "ttc", "trying_to_conceive")
              else None)
    stage_key = cache.stage_key_for(
        week, trimester, child_age_months,
        stage=stage, chapter=chapter, ttc_path=ttc_path,
        timing_ownership=timing_ownership, treatment_step=treatment_step,
        pregnancy_ended=pregnancy_ended, family_stage=family_stage,
        previous_loss=previous_loss,
        corrected_months=corrected, signals=signals, conditions=conditions,
        window_source=window,
    )
    # ":w1" = may be answered from the whole library (2026-10-31, pick_results).
    # Answers cached under the stage-only scope could be a web answer or "not in
    # our content" for a question another stage covers; a new key means none of
    # them is served again. Kept for revert: the key without ":w1".
    if may_widen:
        stage_key = f"{stage_key}:w1"
    is_ttc = (stage or "").lower() in ("trying", "ttc", "trying_to_conceive")
    personal = cache.is_personal(question)  # her-own-data questions are never cached

    # 4) Cache (exact → semantic). A hit returns the whole cached response.
    if not personal:
        hit = cache.lookup(question, stage_key, q_vector)
        if hit:
            usage.log_usage(channel=channel, user_key=user_key, cache_hit=True)
            p = hit["payload"]
            return _response(
                p["answer"], f"cache:{hit['match']}", meaning=p["meaning"],
                actions=p["actions"], cache_hit=True,
                secs={k: p[k] for k in ("content", "videos", "products", "services")},
            )

    # 5) ONE wide semantic search — feeds BOTH the answer and the sections.
    results = retrieve(question, top_k=settings.sections_retrieval_k,
                       domain=domain, q_vector=q_vector)
    if may_widen:
        # A second vector search, no second embedding: costs a database query,
        # never an LLM call.
        wide = retrieve(question, top_k=settings.sections_retrieval_k,
                        domain=None, q_vector=q_vector)
        results, widened = pick_results(
            results, wide, floor=settings.min_retrieval_similarity)
        if widened:
            log.info("ask: widened beyond stage=%s for a better match", stage)
    top_sim = results[0]["similarity"] if results else 0.0
    stage_note = (
        describe_ttc_stage(chapter, ttc_path, months_trying, cycle_day,
                           timing_ownership=timing_ownership,
                           treatment_step=treatment_step,
                           window_source=window)
        if is_ttc
        else describe_stage(week, trimester, child_age_months,
                            pregnancy_ended=pregnancy_ended,
                            family_stage=family_stage)
    )
    stage_note = with_previous_loss(
        stage_note, previous_loss=previous_loss, is_ttc=is_ttc,
        pregnancy_ended=pregnancy_ended, child_age_months=child_age_months)
    if not is_ttc:
        stage_note = with_child_context(
            stage_note, child_age_months=child_age_months,
            corrected_months=corrected, signals=signals, conditions=conditions)

    # 6) Confidence floor — nothing of ours is close. Record the gap, try the web.
    if top_sim < settings.min_retrieval_similarity:
        gaps.log_gap(question, stage_key)
        web, billed = _try_trusted_web(
            question, user_key=user_key, channel=channel, stage_key=stage_key,
            stage_note=stage_note, q_vector=q_vector, personal=personal)
        if web:
            return web
        if not billed:
            usage.log_usage(channel=channel, user_key=user_key, cache_hit=False)
        return _response(_MSG_LOW_CONF, "low_confidence", top_similarity=top_sim)

    # Build the pointer sections from the same results (enrich doc_ids for deep-link).
    sections.enrich_doc_ids(results)
    secs = sections.build_sections(results, lang=lang)
    sections.attach_bodies(secs["content"])  # full body so the reader shows the real article

    # 7) Generate sections 1–3 from the top chunks (the only step that costs money).
    result = _complete_or_none(build_full_messages(
        question, results[: settings.answer_context_k], stage_note=stage_note))
    if result is None:
        # The reads we found cost nothing and are still right; show them.
        return _response(_MSG_RESTING, "llm_unavailable", secs=secs,
                         top_similarity=top_sim)
    cost = _cost_usd(result["input_tokens"], result["output_tokens"])
    usage.log_usage(channel=channel, user_key=user_key, cache_hit=False,
                    input_tokens=result["input_tokens"],
                    output_tokens=result["output_tokens"], cost_usd=cost)
    parsed = parse_structured(result["text"])

    # 7b) The model read our content and still couldn't answer → gap, then web.
    if _is_no_answer(parsed["answer"]):
        gaps.log_gap(question, stage_key)
        web, _ = _try_trusted_web(
            question, user_key=user_key, channel=channel, stage_key=stage_key,
            stage_note=stage_note, q_vector=q_vector, personal=personal)
        if web:
            return web
        return _response(_MSG_LOW_CONF, "no_answer", top_similarity=top_sim, cost_usd=cost)

    # 8) Cache the FULL structured response (future repeats at this stage are free).
    payload = {"answer": parsed["answer"], "meaning": parsed["meaning"],
               "actions": parsed["actions"], **secs}
    if not personal:
        cache.store(question, stage_key, q_vector, payload)

    return _response(parsed["answer"], "llm", meaning=parsed["meaning"],
                     actions=parsed["actions"], secs=secs, top_similarity=top_sim,
                     cost_usd=cost, input_tokens=result["input_tokens"],
                     output_tokens=result["output_tokens"])
