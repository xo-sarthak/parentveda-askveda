"""
The brain (Phase 4) — one function that turns a question into an answer, wiring
together guardrails → cache → retrieval → LLM → logging in the right order.

This `answer()` is the single code path BOTH doors call later: the app endpoint
(Phase 5) and the WhatsApp webhook (Phase 6) just normalize their input and call
here. "One brain, two doors" lives in this file.

Order matters — cheapest/safest checks first, the expensive LLM call last:
  1. rate limit      (reject cheaply)
  2. spend cap       (global circuit breaker)
  3. red-flag        (safety — skip RAG)
  4. cache           (exact → semantic; a hit is ~₹0)
  5. retrieve        (top-k chunks)
  6. confidence floor(nothing close → decline WITHOUT paying for the LLM)
  7. generate        (the only step that costs money)
  8. store + log
"""

from app import cache, flywheel, gaps, guardrails, usage, web_fallback
from app.config import settings
from app.embeddings import embed_query
from app.llm import complete
from app.prompt import NO_ANSWER, build_messages, build_web_messages, describe_stage
from app.retriever import retrieve

# Canned, friendly responses for the non-answer paths.
_MSG_EMPTY = "Please type a question and I'll do my best to help."
_MSG_THROTTLED = (
    "You've reached today's question limit. Please come back tomorrow — I'll be here."
)
_MSG_BUSY = (
    "I'm taking a short breather to keep things running smoothly. "
    "Please try again in a little while."
)
_MSG_LOW_CONF = (
    "I don't have solid information on that in my content yet. It's best to check "
    "with your doctor, or try rephrasing your question."
)


# The model writes a prose non-answer instead of the sentinel often enough that we
# can't depend on the sentinel alone. These are the shapes it actually produces.
_DECLINE_VERBS = (
    "doesn't mention", "does not mention",
    "doesn't cover", "does not cover",
    "doesn't contain", "does not contain",
    "doesn't provide", "does not provide",
    "doesn't include", "does not include",
    "doesn't specify", "does not specify",
)
_DECLINE_PHRASES = (
    "i don't have information", "i do not have information",
    "i don't have any information", "i don't have that",
)


def _is_no_answer(text: str) -> bool:
    """Did the model signal that our material doesn't actually cover this?

    Three detectors, because a small model is NOT reliably obedient about output
    format. We ask for a `NO_ANSWER` sentinel, but in practice it also (a) buries
    the sentinel after a sentence of preamble, or (b) skips it entirely and writes
    "the content provided doesn't mention X". All three count as a content gap —
    which is what routes her to the trusted-web fallback instead of a dead end.
    """
    t = (text or "").lower()
    if NO_ANSWER.lower() in t:
        return True
    # "...the content doesn't mention obstetric cholestasis" — scoped to sentences
    # about OUR material, so a genuine answer like "your report doesn't mention
    # any abnormality" isn't misread as a gap.
    if "content" in t and any(v in t for v in _DECLINE_VERBS):
        return True
    return any(p in t for p in _DECLINE_PHRASES)


def _cost_usd(input_tokens: int, output_tokens: int) -> float:
    """Reference cost from tokens × configured per-million prices (see config)."""
    return round(
        input_tokens / 1_000_000 * settings.llm_price_input_per_1m_usd
        + output_tokens / 1_000_000 * settings.llm_price_output_per_1m_usd,
        6,
    )


def _try_trusted_web(
    question: str,
    *,
    user_key: str,
    channel: str,
    stage_key: str,
    stage_note: str | None,
    q_vector: list[float],
    personal: bool,
) -> tuple[dict | None, bool]:
    """Last resort before dead-ending: answer from WHITELISTED health authorities.

    Returns (result_or_None, made_llm_call). The bool lets the caller avoid
    double-logging usage when we already billed for an attempt here.
    """
    results = web_fallback.search_trusted(question)
    if not results:
        return None, False

    out = complete(build_web_messages(question, results, stage_note=stage_note))
    cost = _cost_usd(out["input_tokens"], out["output_tokens"])
    usage.log_usage(
        channel=channel,
        user_key=user_key,
        cache_hit=False,
        used_web=True,
        input_tokens=out["input_tokens"],
        output_tokens=out["output_tokens"],
        cost_usd=cost,
    )

    text = out["text"].strip()
    if _is_no_answer(text):
        return None, True  # even trusted sources didn't cover it

    # She got a real answer. Cache it — which is what makes this expensive path
    # SELF-EXTINGUISHING: the next asker at this stage gets it for ~₹0.
    if not personal:
        cache.store(question, stage_key, q_vector, text)

    # …and hand a draft to the editors, so next time we OWN this answer.
    if settings.flywheel_enabled:
        flywheel.draft_from_gap(question, text, results, stage_key)

    return {
        "answer": text,
        "source": "web",
        "cache_hit": False,
        "used_web": True,
        "cost_usd": cost,
        "sources": [r["url"] for r in results],
    }, True


def answer(
    question: str,
    *,
    user_key: str,
    channel: str = "app",
    week: int | None = None,
    trimester: str | None = None,
    child_age_months: int | None = None,
    domain: str | None = None,
) -> dict:
    """Answer one question end to end. Returns {answer, source, cache_hit, …}.

    `source` says which path produced it: rate_limited | spend_capped | red_flag |
    cache:exact | cache:semantic | low_confidence | llm.
    """
    question = (question or "").strip()
    if not question:
        return {"answer": _MSG_EMPTY, "source": "empty", "cache_hit": False}

    # 1) Per-user rate limit (don't log rejections — they aren't answers).
    if not guardrails.within_rate_limit(user_key):
        return {"answer": _MSG_THROTTLED, "source": "rate_limited", "cache_hit": False}

    # 2) Global daily spend cap (circuit breaker).
    if not guardrails.within_spend_cap():
        return {"answer": _MSG_BUSY, "source": "spend_capped", "cache_hit": False}

    # 3) Red-flag routing — possible emergency, skip RAG, calm doctor note.
    rf = guardrails.red_flag_response(question)
    if rf:
        usage.log_usage(channel=channel, user_key=user_key, cache_hit=False)
        return {"answer": rf, "source": "red_flag", "cache_hit": False}

    # Embed ONCE — reused by the cache lookup AND retrieval (no double work).
    q_vector = embed_query(question)
    stage_key = cache.stage_key_for(week, trimester, child_age_months)

    # Questions about HER OWN data are never cached (wrong for anyone else, and a
    # privacy smell) — they always go fresh.
    personal = cache.is_personal(question)

    # 4) Cache (exact → semantic). A hit skips retrieval + the LLM.
    if not personal:
        hit = cache.lookup(question, stage_key, q_vector)
        if hit:
            usage.log_usage(channel=channel, user_key=user_key, cache_hit=True)
            return {"answer": hit["answer"], "source": f"cache:{hit['match']}", "cache_hit": True}

    # 5) Retrieve the closest content chunks.
    chunks = retrieve(question, domain=domain, q_vector=q_vector)
    top_sim = chunks[0]["similarity"] if chunks else 0.0
    stage_note = describe_stage(week, trimester, child_age_months)

    # 6) Confidence floor — nothing of ours is close enough. Record the CONTENT
    #    GAP, then try the trusted-web fallback before dead-ending on her.
    if top_sim < settings.min_retrieval_similarity:
        gaps.log_gap(question, stage_key)
        web, billed = _try_trusted_web(
            question, user_key=user_key, channel=channel, stage_key=stage_key,
            stage_note=stage_note, q_vector=q_vector, personal=personal,
        )
        if web:
            return web
        if not billed:
            usage.log_usage(channel=channel, user_key=user_key, cache_hit=False)
        return {
            "answer": _MSG_LOW_CONF,
            "source": "low_confidence",
            "cache_hit": False,
            "top_similarity": top_sim,
        }

    # 7) Generate — the only step that costs money. `stage_note` is CONTEXT (so the
    #    tense is right for where she is), never a filter on what she may ask.
    result = complete(build_messages(question, chunks, stage_note=stage_note))
    cost = _cost_usd(result["input_tokens"], result["output_tokens"])
    text = result["text"]

    usage.log_usage(
        channel=channel,
        user_key=user_key,
        cache_hit=False,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cost_usd=cost,
    )

    # 7b) The model read our content and said it doesn't cover this → CONTENT GAP.
    #     Log it, try the trusted web, and never cache a decline.
    if _is_no_answer(text):
        gaps.log_gap(question, stage_key)
        web, _ = _try_trusted_web(
            question, user_key=user_key, channel=channel, stage_key=stage_key,
            stage_note=stage_note, q_vector=q_vector, personal=personal,
        )
        if web:
            return web
        return {
            "answer": _MSG_LOW_CONF,
            "source": "no_answer",
            "cache_hit": False,
            "top_similarity": top_sim,
            "cost_usd": cost,
        }

    # 8) Store in cache (future repeats at this stage are free).
    if not personal:
        cache.store(question, stage_key, q_vector, text)

    return {
        "answer": text,
        "source": "llm",
        "cache_hit": False,
        "top_similarity": top_sim,
        "cost_usd": cost,
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
    }
