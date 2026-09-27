"""
LLM client (Phase 3) — the "G" in RAG (Generation).

One thin wrapper around an OpenAI-COMPATIBLE chat API. We point it at Groq via
`llm_base_url`; because Groq (and Together, and others) speak the same API shape,
switching providers is just changing the base_url + model in config — no code
change. That's why we deliberately use the `openai` client rather than a
Groq-specific SDK.
"""

from openai import OpenAI

from app.config import settings

# One shared client. The `base_url` is what makes this talk to Groq instead of
# OpenAI; the api_key is your gsk_... key from .env.
_client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


def complete(messages: list[dict]) -> dict:
    """Send chat messages to the LLM; return {'text', 'input_tokens',
    'output_tokens'}.

    We return token counts too (not just the text) because Phase 4 uses them to
    log cost per answer and enforce the daily spend cap. Low temperature keeps
    answers factual and consistent — we want grounded, not creative.
    """
    resp = _client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=settings.llm_temperature,
        **_reasoning_args(),
    )
    usage = resp.usage
    return {
        "text": (resp.choices[0].message.content or "").strip(),
        "input_tokens": getattr(usage, "prompt_tokens", 0),
        "output_tokens": getattr(usage, "completion_tokens", 0),
    }


def _reasoning_args() -> dict:
    """`reasoning_effort` for the models that take it (gpt-oss), nothing for the
    rest, so switching back to a non-reasoning model is still one config line.
    Sent through `extra_body` so it works whatever version of the openai client
    is installed."""
    if "gpt-oss" in settings.llm_model:
        return {"extra_body": {"reasoning_effort": settings.llm_reasoning_effort}}
    return {}


def probe() -> dict:
    """Ask the configured model for one word; say plainly whether it answered.

    WHY THIS EXISTS (2026-09-27): Groq retired `llama-3.1-8b-instant` and every
    answer failed with "model not found" for days before anyone noticed. A
    hosted model's name is a dependency with an expiry date; this is the cheapest
    possible check that it still resolves (about 20 tokens). It is called at
    startup, by GET /health/llm and by scripts/llm_smoke.py (the daily job).

    Never raises: returns {'ok', 'model', 'reason', 'detail'} so every caller
    can report the same words.
    """
    model = settings.llm_model
    try:
        resp = _client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word OK."}],
            temperature=0,
            # Room for a reasoning model's hidden thinking before its one word:
            # at 16 tokens gpt-oss spent them all thinking and replied ''.
            max_tokens=128,
            **_reasoning_args(),
        )
        text = (resp.choices[0].message.content or "").strip()
        return {"ok": True, "model": model, "reason": "answered", "detail": text[:40]}
    except Exception as e:  # noqa: BLE001 - a probe reports, it never crashes
        return {"ok": False, "model": model, "reason": _reason(e), "detail": str(e)[:300]}


def _reason(e: Exception) -> str:
    """The plain cause, from the provider's error, for a log line or an alert."""
    name = type(e).__name__
    text = str(e).lower()
    if name == "NotFoundError" or "model_not_found" in text or "decommissioned" in text:
        return "model retired or unknown: change LLM_MODEL"
    if name == "AuthenticationError" or "invalid api key" in text:
        return "API key rejected: check LLM_API_KEY"
    if name == "RateLimitError" or "rate limit" in text:
        return "rate limited: free-tier limit reached"
    if name in ("APIConnectionError", "APITimeoutError"):
        return "provider unreachable"
    return f"unexpected error ({name})"

