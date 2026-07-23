"""
The content flywheel (Phase 7) — turn every gap into owned content.

The loop:
    a mother asks something our content can't answer
        → we answer her from a trusted source (she is never dead-ended)
        → the question is logged as a GAP (veda_content_gaps, with ask_count)
        → the answer is written here as a DRAFT for a human to review
        → an editor edits + publishes it as real ParentVeda content
        → the NEXT mother gets a cheap, grounded, OWNED answer

Each failure quietly becomes an asset, and the expensive web path is
self-extinguishing: the more it fires, the less it needs to.

Rule that is never bent: **we never auto-publish medical content.** Drafts land in
their own table marked `pending`, with their source URLs, for a human to check.
"""

from app.cache import normalize
from app.db import supabase


def draft_from_gap(
    question: str,
    draft_body: str,
    sources: list[dict],
    stage_key: str = "",
) -> None:
    """Save a machine-written draft answer for editorial review. Best-effort."""
    q = (question or "").strip()
    body = (draft_body or "").strip()
    if not q or not body:
        return
    try:
        supabase.table("veda_drafts").upsert(
            {
                "question_norm": normalize(q),
                "question": q,
                "draft_body": body,
                # Keep only what a reviewer needs to verify the claim.
                "sources": [
                    {"title": s.get("title"), "url": s.get("url")} for s in sources
                ],
                "stage_key": stage_key or "",
                "status": "pending",  # NEVER 'published' — a human decides that
            },
            on_conflict="question_norm",
        ).execute()
    except Exception as e:  # pragma: no cover - bookkeeping must not break answers
        print(f"[flywheel.draft_from_gap] non-fatal: {e}")
