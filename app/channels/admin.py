"""
Admin door (Phase 8) — POST /reindex and GET /metrics.

/reindex closes the flywheel loop: when content is published/edited in Directus,
its webhook hits this endpoint and we re-embed so the change becomes searchable.
/metrics exposes cache-hit rate, cost and gaps so the system is observable.

Both are guarded by `reindex_secret` — they're public URLs that do real work.
"""

from fastapi import APIRouter, BackgroundTasks, Header, Request

from app import metrics
from app.config import settings
from ingest.ingest import ingest, reindex_source

router = APIRouter()


def _authorized(secret: str | None) -> bool:
    # If no secret is configured (local dev), allow. In production, set one.
    return not settings.reindex_secret or secret == settings.reindex_secret


@router.post("/reindex")
async def reindex(
    request: Request,
    background_tasks: BackgroundTasks,
    x_reindex_secret: str | None = Header(default=None),
) -> dict:
    if not _authorized(x_reindex_secret):
        return {"status": "unauthorized"}

    try:
        body = await request.json()
    except Exception:
        body = {}

    # Accept both our shape {source_table, source_id|keys} and a Directus-style
    # {collection, keys:[...]} payload.
    table = body.get("source_table") or body.get("collection")
    ids = body.get("source_id") or body.get("keys")
    if isinstance(ids, (str, int)):
        ids = [ids]

    # INCREMENTAL — one or a few rows. Fast enough to do inline.
    if table and ids:
        results = [reindex_source(table, str(i)) for i in ids]
        return {"status": "ok", "mode": "incremental", "results": results}

    # FULL — re-embed everything. Minutes long, so run it in the BACKGROUND and
    # return immediately (a webhook that waits would time out and be retried).
    background_tasks.add_task(ingest)
    return {"status": "accepted", "mode": "full", "note": "re-embedding in background"}


@router.get("/metrics")
def get_metrics(
    days: int = 7,
    x_reindex_secret: str | None = Header(default=None),
) -> dict:
    if not _authorized(x_reindex_secret):
        return {"status": "unauthorized"}
    return metrics.full_report(days)
