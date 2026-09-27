"""
AskVeda — FastAPI entry point (the "front door" of the whole service).

Uvicorn (the web server) imports the `app` object from this file and serves it.
Every HTTP request — from the Flutter app or from WhatsApp — eventually hits a
route defined on this `app`.

Routes so far: `/health` (Phase 0) and `POST /ask` (Phase 5, the app door). The
WhatsApp webhook arrives in Phase 6 — it will call the SAME brain as `/ask`.
"""

import logging
import threading

from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware

from app.channels.admin import _authorized
from app.channels.admin import router as admin_router
from app.channels.app_api import router as app_router
from app.channels.whatsapp import router as whatsapp_router
from app.config import settings
from app.llm import probe

log = logging.getLogger("askveda")

# `app` IS the application (an ASGI app object). FastAPI builds it; uvicorn runs it.
app = FastAPI(title=settings.app_name, version="0.1.0")

# CORS: a browser (e.g. Flutter web) blocks cross-origin calls unless the server
# allows them. In local dev we allow everything so testing is painless; production
# should be locked to the real app origins. (Native mobile apps ignore CORS.)
if settings.environment == "local":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Mount the two doors. Both call the same brain (app/answer.py).
app.include_router(app_router)        # POST /ask               — the Flutter app
app.include_router(whatsapp_router)   # POST /whatsapp/webhook  — MSG91 inbound
app.include_router(admin_router)      # POST /reindex, GET /metrics — ops


@app.get("/health")
def health() -> dict:
    """A trivial 'is the service alive?' endpoint.

    Hosts like Render ping an endpoint like this to check the service is up. It
    also lets *us* confirm, right now, that the whole setup works end to end.
    A GET request to /health runs this function and returns the dict as JSON.
    """
    return {
        "status": "ok",
        "service": settings.app_name,
        "env": settings.environment,
    }


@app.on_event("startup")
def _check_llm_on_startup() -> None:
    """Say loudly, once, if the configured model no longer answers (2026-09-27).

    In a thread, so a slow provider never delays the service coming up. The
    service still starts either way: the offline answer engine in the app keeps
    working, and /health stays green for the host's liveness checks.
    """
    def run() -> None:
        r = probe()
        if r["ok"]:
            log.warning("[llm] %s answers", r["model"])
        else:
            log.error("[llm] %s DOES NOT ANSWER: %s. Every Ask Veda answer will "
                      "fail until this is fixed. %s", r["model"], r["reason"], r["detail"])
            print(f"\n!!! [llm] {r['model']} does not answer: {r['reason']}\n")

    threading.Thread(target=run, daemon=True).start()


@app.get("/health/llm")
def health_llm(x_reindex_secret: str | None = Header(default=None)) -> dict:
    """Does the configured model answer right now? Guarded by the admin secret,
    because each call spends a few tokens and this is a public URL."""
    if not _authorized(x_reindex_secret):
        return {"status": "unauthorized"}
    return probe()

