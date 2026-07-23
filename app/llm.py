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
    )
    usage = resp.usage
    return {
        "text": (resp.choices[0].message.content or "").strip(),
        "input_tokens": getattr(usage, "prompt_tokens", 0),
        "output_tokens": getattr(usage, "completion_tokens", 0),
    }
