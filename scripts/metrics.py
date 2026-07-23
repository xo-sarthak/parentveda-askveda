"""
Metrics report (Phase 8) — a glance at the health of the whole system.

    python -m scripts.metrics [days]
"""

import sys

from app import metrics


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    rep = metrics.full_report(days)
    s = rep["summary"]

    print(f"\n=== AskVeda — last {s['window_days']} day(s) ===")
    print(f"  answers               : {s['answers']}")
    print(f"  cache-hit rate        : {s['cache_hit_rate'] * 100:.1f}%   (higher = cheaper)")
    print(f"  web-fallback rate     : {s['web_fallback_rate'] * 100:.1f}%   (higher = more content gaps)")
    print(f"  LLM answers (billable): {s['llm_answers']}")
    print(f"  total cost            : ${s['total_cost_usd']}")
    print(f"  avg cost / answer     : ${s['avg_cost_per_answer_usd']}")
    print(f"  spend today           : ${s['spend_today_usd']} / ${s['daily_spend_cap_usd']} cap "
          f"({s['answers_today']} answers)")

    print("\n  Top content gaps (write these next):")
    gaps = rep["top_gaps"]
    if not gaps:
        print("    (none yet)")
    for g in gaps:
        print(f"    {g['ask_count']:>3}×  [{g.get('stage_key') or '-'}]  {g['question']}")

    print("\n  Top cached questions (the real FAQs):")
    cached = rep["top_cached"]
    if not cached:
        print("    (none yet)")
    for c in cached:
        print(f"    {c['hit_count']:>3}×  [{c.get('week_key') or '-'}]  {c['cached_question']}")
    print()


if __name__ == "__main__":
    main()
