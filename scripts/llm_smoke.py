"""
Does the configured model still answer?   python -m scripts.llm_smoke

Exits 0 when it does, 1 when it does not, with the reason in plain words. The
daily GitHub Actions job (.github/workflows/llm-smoke.yml) runs exactly this, so
a retired model shows up as a failed job and an email, not as days of silent
"no answer" in the app. Costs about 20 tokens a run.

Written 2026-09-27, after Groq retired `llama-3.1-8b-instant`.
"""

import sys

from app.llm import probe


def main() -> int:
    r = probe()
    if r["ok"]:
        print(f"OK: {r['model']} answers ({r['detail']!r})")
        return 0
    print(f"FAIL: {r['model']} does not answer: {r['reason']}")
    print(f"      {r['detail']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
