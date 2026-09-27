"""
App door (Phase 5) — POST /ask.

The Flutter app's Ask Veda screen calls this endpoint. It does three small things:
resolve who's asking (auth → user_key), call the ONE brain (`answer()`), and return
JSON. There is deliberately NO RAG logic here — this file is purely the channel
adapter for the app. (WhatsApp gets its own adapter in Phase 6, calling the same
brain.)
"""

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict

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
    # Trying-to-conceive context (all optional, all framing-only).
    stage: str | None = None             # trying | pregnancy | parenting
    chapter: str | None = None           # preparingTogether … aNewBeginning
    cycle_day: int | None = None         # NEVER sent from the partner's app
    ttc_path: str | None = None          # natural | iui | ivf | …
    # parentveda | clinic_guided | clinic_controlled — who owns this cycle's timing
    timing_ownership: str | None = None
    # Where a clinic round stands today (the app's TtcRoundPhase name:
    # gettingReady … waiting, testDay, result, betweenRounds). Sent only while a
    # clinic owns the cycle; decides whether "not pregnant" is true, unknown or
    # false. Never sent from the partner's app.
    treatment_step: str | None = None
    months_trying: int | None = None     # the strongest TTC personalisation signal
    lang: str | None = None              # 'en' | 'hi' — which bilingual twin to show
    domain: str | None = None            # optional retrieval hint, not a gate

    # KEEP unknown fields instead of discarding them, so `unknown_fields()` can
    # report them. Pydantic's default is to drop silently — which is exactly how
    # the app added `timing_ownership`, sent it for days, and had it do nothing
    # with no error anywhere. The wire body is a contract across TWO repos; when
    # only one side has been updated, that must be visible.
    model_config = ConfigDict(extra="allow")

    def unknown_fields(self) -> list[str]:
        """Fields the app sent that this service does not understand (yet)."""
        return sorted((self.model_extra or {}).keys())


class SectionItem(BaseModel):
    """One pointer card — display fields + the identity the app deep-links with."""
    doc_id: str | None = None
    source_table: str | None = None
    source_id: str | None = None
    kind: str | None = None
    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    body: str | None = None       # full text for content items (the reader shows this)
    similarity: float | None = None


class AskResponse(BaseModel):
    # Sections 1–3 (from the LLM)
    answer: str                       # S1 Veda Answer
    meaning: str = ""                 # S2 What this means for you
    actions: list[str] = []           # S3 Recommended next actions
    # Sections 4/6/7 + videos (pointers; empty list → app shows "Coming soon")
    content: list[SectionItem] = []   # S4 More information
    videos: list[SectionItem] = []    # (sub-section of S4)
    products: list[SectionItem] = []  # S6 Products
    services: list[SectionItem] = []  # S7 Services
    # meta
    source: str
    cache_hit: bool


@router.post("/ask", response_model=AskResponse)
def ask(
    body: AskRequest,
    authorization: str | None = Header(default=None),
    x_user_key: str | None = Header(default=None),
) -> AskResponse:
    user_key = resolve_user_key(authorization, x_user_key)
    # Loud, not silent: the app is sending something we don't handle yet.
    unknown = body.unknown_fields()
    if unknown:
        print(f"[/ask] app sent field(s) this service ignores: {unknown} "
              f"— add them to AskRequest + answer() or they do nothing.")
    result = answer(
        body.question,
        user_key=user_key,
        channel="app",
        week=body.week,
        trimester=body.trimester,
        child_age_months=body.child_age_months,
        stage=body.stage,
        chapter=body.chapter,
        cycle_day=body.cycle_day,
        ttc_path=body.ttc_path,
        timing_ownership=body.timing_ownership,
        treatment_step=body.treatment_step,
        months_trying=body.months_trying,
        lang=body.lang,
        domain=body.domain,
    )
    return AskResponse(
        answer=result["answer"],
        meaning=result.get("meaning", ""),
        actions=result.get("actions", []),
        content=result.get("content", []),
        videos=result.get("videos", []),
        products=result.get("products", []),
        services=result.get("services", []),
        source=result["source"],
        cache_hit=result["cache_hit"],
    )
