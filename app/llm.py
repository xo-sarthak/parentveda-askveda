"""
LLM client (Phase 3) — the "G" in RAG (Generation).

One thin wrapper around an OpenAI-COMPATIBLE chat API. We point it at Groq via
`llm_base_url`; because Groq (and Together, and others) speak the same API shape,
switching providers is just changing the base_url + model in config — no code
change. That's why we deliberately use the `openai` client rather than a
Groq-specific SDK.
"""

import anthropic
from openai import OpenAI

from app.config import settings
from app.prompt import NO_ANSWER

# One shared client. The `base_url` is what makes this talk to Groq instead of
# OpenAI; the api_key is your gsk_... key from .env.
_client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)

# Claude (2026-10-08), used when LLM_PROVIDER=anthropic. Built lazily so a
# missing key only matters when Claude is actually the provider; with Groq
# active this stays None and costs nothing.
_claude: anthropic.Anthropic | None = None

# Server-side fallback: if Claude's safety classifiers decline a request, the
# API re-runs it on Anthropic's recommended substitute model and returns that
# answer, in one round trip. A pregnancy or fertility question can brush the
# "bio" classifier by accident; without this the parent gets nothing.
# Only some models take it: Haiku 5.5 has no server-side fallback, so the
# parameter is sent only to the Opus / Sonnet / Fable families. On Haiku a
# decline still lands safely, as NO_ANSWER (see _complete_claude).
_FALLBACK_HEADERS = {"anthropic-beta": "server-side-fallback-2026-07-01"}
_FALLBACK_BODY = {"fallbacks": "default"}
_FALLBACK_MODELS = ("claude-opus-", "claude-sonnet-", "claude-fable-")


def _fallback_args() -> dict:
    if settings.anthropic_model.startswith(_FALLBACK_MODELS):
        return {"extra_headers": _FALLBACK_HEADERS, "extra_body": _FALLBACK_BODY}
    return {}


def _claude_client() -> anthropic.Anthropic:
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _claude


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI-style [system, user] -> Claude's shape. The Messages API takes
    the system prompt as its own `system` field, not as a message, so the
    prompt builders keep their one shape and only this edge translates."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [m for m in messages if m["role"] != "system"]
    return system, rest


def _claude_create(messages: list[dict], max_tokens: int):
    system, rest = _split_system(messages)
    return _claude_client().messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=rest,
        output_config={"effort": settings.anthropic_effort},
        **_fallback_args(),
    )


def _claude_text(resp) -> str:
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def _complete_claude(messages: list[dict]) -> dict:
    resp = _claude_create(messages, settings.anthropic_max_tokens)
    # A decline is a normal 200 with stop_reason "refusal" and maybe no text.
    # It must read as "no answer", which sends the question down the existing
    # gap -> trusted-web path. Read as text, an empty answer would be shown to
    # her AND cached for everyone asking the same thing.
    if resp.stop_reason == "refusal":
        text = NO_ANSWER
    else:
        text = _claude_text(resp) or NO_ANSWER
    return {
        "text": text,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }


def complete(messages: list[dict]) -> dict:
    """Send chat messages to the LLM; return {'text', 'input_tokens',
    'output_tokens'}.

    We return token counts too (not just the text) because Phase 4 uses them to
    log cost per answer and enforce the daily spend cap. Low temperature keeps
    answers factual and consistent — we want grounded, not creative.
    """
    if settings.llm_provider == "anthropic":
        return _complete_claude(messages)
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
    model = settings.active_model
    try:
        if settings.llm_provider == "anthropic":
            # Claude 5.5 models think a little first; 1024 leaves room for that
            # before the one word (the gpt-oss lesson below, again).
            resp = _claude_create(
                [{"role": "user", "content": "Reply with the single word OK."}], 1024)
            text = _claude_text(resp)
            return {"ok": True, "model": model, "reason": "answered", "detail": text[:40]}
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
        return f"model retired or unknown: change {_env('MODEL')}"
    if (name == "AuthenticationError" or "invalid api key" in text
            or "api_key" in text or "x-api-key" in text):
        return f"API key rejected: check {_env('API_KEY')}"
    if "credit balance" in text:
        return "no credits: add credits in Console under Plans & Billing"
    if name == "RateLimitError" or "rate limit" in text:
        return "rate limited: free-tier limit reached"
    if name in ("APIConnectionError", "APITimeoutError"):
        return "provider unreachable"
    return f"unexpected error ({name})"


def _env(suffix: str) -> str:
    """The .env line to fix for the ACTIVE provider, so an alert names the
    variable that is actually wrong (LLM_API_KEY vs ANTHROPIC_API_KEY)."""
    return ("ANTHROPIC_" if settings.llm_provider == "anthropic" else "LLM_") + suffix
