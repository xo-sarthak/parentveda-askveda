"""
Model benchmark (Phase 8) — lock the model with evidence, not a guess.

Runs the SAME grounded prompt through several Groq models on real questions, so
you can compare answer QUALITY (you judge), LATENCY and COST side by side. Because
the provider is a one-line config swap, switching is trivial once you've decided.

    python -m scripts.benchmark

Bypasses the cache on purpose (calls the LLM directly) — we're measuring the
models, not the cache.
"""

import sys
import time

from openai import OpenAI

from app.config import settings
from app.prompt import build_messages
from app.retriever import retrieve

# Model output can contain emoji/em-dashes the Windows console can't encode — don't
# let that crash the run.
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# Candidates seen live on Groq (see the Phase 3 model list). Edit freely.
MODELS = [
    "llama-3.1-8b-instant",     # our current default — cheapest 8B
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",  # the heavyweight — quality ceiling
    # "qwen/qwen3.6-27b",       # a REASONING model: emits <think>, ~30x cost — unsuitable
]

# Rough Groq $/1M tokens (input, output). VERIFY against groq.com/pricing — these
# move. Used only for a relative cost feel, not billing.
PRICING = {
    "llama-3.1-8b-instant": (0.05, 0.08),
    "openai/gpt-oss-20b": (0.10, 0.50),
    "qwen/qwen3.6-27b": (0.20, 0.60),
    "llama-3.3-70b-versatile": (0.59, 0.79),
}

QUESTIONS = [
    "what happens at the anatomy scan?",
    "can I eat papaya during pregnancy?",
    "when should I start solids?",
    "how do I help my baby sleep through the night?",
]

_client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


def _cost(model: str, pt: int, ct: int) -> float:
    pin, pout = PRICING.get(model, (0.0, 0.0))
    return round(pt / 1e6 * pin + ct / 1e6 * pout, 6)


def main() -> None:
    for q in QUESTIONS:
        chunks = retrieve(q)
        top = chunks[0]["similarity"] if chunks else 0.0
        messages = build_messages(q, chunks)
        print("\n" + "=" * 78)
        print(f"Q: {q}    (top retrieval sim {top:.3f})")
        print("=" * 78)
        for model in MODELS:
            try:
                t0 = time.time()
                resp = _client.chat.completions.create(
                    model=model, messages=messages, temperature=settings.llm_temperature,
                )
                dt = time.time() - t0
                u = resp.usage
                pt, ct = getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0)
                answer = (resp.choices[0].message.content or "").strip()
                print(f"\n--- {model}  |  {dt:.2f}s  |  {pt}in/{ct}out  |  ${_cost(model, pt, ct)} ---")
                print(answer)
            except Exception as e:
                print(f"\n--- {model}  |  ERROR: {e} ---")
    print("\nDone. Read the answers for quality; weigh it against latency + cost.")


if __name__ == "__main__":
    main()
