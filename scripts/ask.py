"""
Dev CLI — ask a question through the FULL brain (Phase 4).

    python -m scripts.ask "what happens at the anatomy scan?"

Now routes through app.answer.answer(), so it exercises the whole pipeline:
guardrails → cache → retrieval → LLM → logging. The printed `source` tells you
which path answered:
    llm            → freshly generated (cost money)
    cache:exact    → same question asked before (instant, ~₹0)
    cache:semantic → a near-identical question was cached (instant, ~₹0)
    red_flag       → possible emergency → calm doctor routing
    low_confidence → nothing in content was close enough
    rate_limited / spend_capped → a guardrail stopped it

Tip: ask the SAME question twice — the 2nd time comes back as cache:exact, fast,
with no tokens spent.
"""

import sys
import time

from app.answer import answer


def main() -> None:
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        print('Usage: python -m scripts.ask "your question here"')
        return

    print(f"\nQ: {question}\n")

    t0 = time.time()
    res = answer(question, user_key="dev-cli", channel="app")
    dt = time.time() - t0

    print("=" * 60)
    print(res["answer"])
    print("=" * 60)

    # A compact status line so you can SEE which path ran.
    bits = [f"source={res['source']}", f"cache_hit={res['cache_hit']}", f"time={dt:.2f}s"]
    if "top_similarity" in res:
        bits.append(f"top_sim={res['top_similarity']:.3f}")
    if res.get("source") == "llm":
        bits.append(f"tokens={res['input_tokens']}in/{res['output_tokens']}out")
        bits.append(f"cost=${res['cost_usd']}")
    print("[" + "  ".join(bits) + "]")


if __name__ == "__main__":
    main()
