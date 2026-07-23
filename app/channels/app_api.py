"""
App door (Phase 5) — POST /ask.

The Flutter app's Ask Veda screen calls this endpoint. It does three small things:
resolve who's asking (auth → user_key), call the ONE brain (`answer()`), and return
JSON. There is deliberately NO RAG logic here — this file is purely the channel
adapter for the app. (WhatsApp gets its own adapter in Phase 6, calling the same
brain.)
"""

from fastapi import APIRouter, Header
from pydantic import BaseModel

from app.answer import answer
from app.auth import resolve_user_key

router = APIRouter()


class AskRequest(BaseModel):
    question: str
    # Where she is in her journey. This is CONTEXT (so the answer is framed in the
    # right tense) and the cache's stage bucket — never a filter on what she may ask.
    week: int | None = None              # pregnancy week
    trimester: str | None = None         # pregnancy trimester (if week unknown)
    child_age_months: int | None = None  # parenting: baby already born
    domain: str | None = None            # optional retrieval hint, not a gate


class AskResponse(BaseModel):
    answer: str
    source: str        # llm | cache:exact | cache:semantic | red_flag | low_confidence | …
    cache_hit: bool


@router.post("/ask", response_model=AskResponse)
def ask(
    body: AskRequest,
    authorization: str | None = Header(default=None),
    x_user_key: str | None = Header(default=None),
) -> AskResponse:
    user_key = resolve_user_key(authorization, x_user_key)
    result = answer(
        body.question,
        user_key=user_key,
        channel="app",
        week=body.week,
        trimester=body.trimester,
        child_age_months=body.child_age_months,
        domain=body.domain,
    )
    return AskResponse(
        answer=result["answer"],
        source=result["source"],
        cache_hit=result["cache_hit"],
    )
