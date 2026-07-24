"""
Dev CLI — ask a question through the full brain and SEE all 7 sections.

    python -m scripts.ask "what happens at the anatomy scan?"

Sections 1-3 (answer/meaning/actions) come from the LLM; 4/6/7 + videos are the
retrieved pointer cards. An empty section is where the app shows "Coming soon".
"""

import sys
import time

from app.answer import answer


def _show_section(name: str, items: list) -> None:
    if not items:
        print(f"  {name}: (empty -> 'Coming soon')")
        return
    print(f"  {name}:")
    for it in items:
        sim = it.get("similarity")
        sim_s = f"{sim:.2f}" if isinstance(sim, (int, float)) else "?"
        print(f"    - [{sim_s}] {it.get('kind')}: {it.get('title')}  (id={it.get('doc_id')})")


def main() -> None:
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        print('Usage: python -m scripts.ask "your question here"')
        return

    print(f"\nQ: {question}\n")
    t0 = time.time()
    r = answer(question, user_key="dev-cli", channel="app", week=20)
    dt = time.time() - t0

    print("=" * 66)
    print("1) VEDA ANSWER:")
    print("  " + r["answer"].replace("\n", "\n  "))
    if r.get("meaning"):
        print("\n2) WHAT THIS MEANS FOR YOU:")
        print("  " + r["meaning"].replace("\n", "\n  "))
    if r.get("actions"):
        print("\n3) RECOMMENDED ACTIONS:")
        for a in r["actions"]:
            print(f"  - {a}")
    print("\n4) MORE INFORMATION")
    _show_section("articles/reads", r.get("content", []))
    _show_section("videos", r.get("videos", []))
    print("6) PRODUCTS")
    _show_section("products", r.get("products", []))
    print("7) SERVICES")
    _show_section("services", r.get("services", []))
    print("=" * 66)

    bits = [f"source={r['source']}", f"cache_hit={r['cache_hit']}", f"time={dt:.2f}s"]
    if r.get("source") == "llm":
        bits.append(f"cost=${r.get('cost_usd')}")
    print("[" + "  ".join(bits) + "]")


if __name__ == "__main__":
    main()
