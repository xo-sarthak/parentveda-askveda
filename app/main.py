"""
AskVeda — FastAPI entry point (the "front door" of the whole service).

Uvicorn (the web server) imports the `app` object from this file and serves it.
Every HTTP request — from the Flutter app or from WhatsApp — eventually hits a
route defined on this `app`.

Routes so far: `/health` (Phase 0) and `POST /ask` (Phase 5, the app door). The
WhatsApp webhook arrives in Phase 6 — it will call the SAME brain as `/ask`.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.channels.admin import router as admin_router
from app.channels.app_api import router as app_router
from app.channels.whatsapp import router as whatsapp_router
from app.config import settings

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
