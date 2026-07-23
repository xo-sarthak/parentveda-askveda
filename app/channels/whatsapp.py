"""
WhatsApp door (Phase 6) — POST /whatsapp/webhook.

The second door onto the SAME brain. When a mother texts your WhatsApp number,
MSG91 (the BSP) receives it from Meta and POSTs it here. We normalize it via the
gateway, call the same `answer()` the app uses, and send the reply back.

Two rules that matter for webhooks:
  • ALWAYS return 200 quickly. If we return an error or hang, the provider retries
    and the mother can get the same answer several times.
  • Ignore non-message events. Delivery/read receipts arrive on this same URL.

Until the MSG91 number is live, the sender runs in MOCK mode (logs instead of
sending), so this whole path is testable today with a simulated payload.
"""

import httpx
from fastapi import APIRouter, Header, Request

from app.answer import answer
from app.config import settings
from app.gateway import parse_msg91_inbound

router = APIRouter()


def send_whatsapp_message(to: str, text: str) -> bool:
    """Send a reply back to the user's WhatsApp number.

    Mock by default. The live call is written but must be confirmed against
    MSG91's current API docs when the account exists — endpoint and body shape
    are provider-specific and we haven't been able to verify them yet.
    """
    if settings.whatsapp_mock_send or not settings.msg91_auth_key:
        preview = text.replace("\n", " ")[:160]
        print(f"[whatsapp MOCK SEND] -> +{to}: {preview}")
        return True

    try:
        res = httpx.post(
            "https://api.msg91.com/api/v5/whatsapp/whatsapp-outbound-message/bulk/",
            headers={
                "authkey": settings.msg91_auth_key,
                "Content-Type": "application/json",
            },
            json={
                "integrated_number": settings.msg91_whatsapp_number,
                "content_type": "text",
                "payload": {
                    "to": to,
                    "type": "text",
                    "text": {"body": text},
                },
            },
            timeout=15,
        )
        ok = res.status_code < 300
        if not ok:
            print(f"[whatsapp SEND FAILED] {res.status_code} {res.text[:200]}")
        return ok
    except Exception as e:
        print(f"[whatsapp SEND ERROR] {e}")
        return False


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    # 1) Reject strangers. Only enforced once a secret is configured, so local
    #    testing stays easy.
    if settings.msg91_webhook_secret and x_webhook_secret != settings.msg91_webhook_secret:
        # Still a 200 — we don't tell an unauthenticated caller anything useful,
        # and we never want the provider retrying on a auth blip.
        return {"status": "unauthorized"}

    try:
        payload = await request.json()
    except Exception:
        return {"status": "bad_payload"}

    # 2) Normalize. None = a receipt/status event, not a question → ignore.
    msg = parse_msg91_inbound(payload)
    if msg is None:
        return {"status": "ignored"}

    # 3) The SAME brain the app calls. No RAG logic lives in this file.
    #    Stage is unknown here: WhatsApp gives us a phone number, not an app
    #    profile. Once phone↔profile linking exists we can pass her week/child age
    #    and get the same stage-aware framing the app enjoys.
    try:
        result = answer(
            msg.text,
            user_key=msg.user_key,
            channel=msg.channel,
            week=msg.week,
            trimester=msg.trimester,
            child_age_months=msg.child_age_months,
        )
    except Exception as e:
        print(f"[whatsapp] answer failed: {e}")
        return {"status": "error"}

    # 4) Reply on the same channel.
    sent = send_whatsapp_message(msg.reply_to or "", result["answer"])

    return {"status": "ok", "source": result["source"], "sent": sent}
