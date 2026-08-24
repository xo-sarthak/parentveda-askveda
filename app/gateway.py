"""
Message Gateway (Phase 6) — normalize any channel into ONE internal shape.

This is the piece that makes "one brain, two doors" literal. The app sends JSON
with a question; WhatsApp sends a provider-shaped webhook payload. Both are
converted here into the same `IncomingMessage`, which is all `answer()` ever sees.
Add a third channel later (SMS, web widget) and only this file grows.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class IncomingMessage:
    """One question, normalized, regardless of where it came from."""

    text: str
    user_key: str          # who asked: 'app:<uuid>' or 'wa:<phone>'
    channel: str           # 'app' | 'whatsapp'
    reply_to: str | None = None   # where the reply goes (phone number for WhatsApp)
    # Stage context (may be unknown on WhatsApp — see channels/whatsapp.py).
    week: int | None = None
    trimester: str | None = None
    child_age_months: int | None = None


def _pick(payload: Any, paths: list[str]) -> str | None:
    """Return the first non-empty value found at any of these dotted paths."""
    for path in paths:
        node: Any = payload
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                node = None
                break
        if isinstance(node, (str, int)) and str(node).strip():
            return str(node).strip()
    return None


def parse_meta_inbound(payload: dict) -> IncomingMessage | None:
    """Parse Meta's WhatsApp Cloud API inbound webhook.

    Use this to test on Meta's FREE test number, before a company, a real
    number or business verification exist. MSG91 resells the same Cloud API, so
    what we learn here is close to what production will send.

    The shape is deeply nested and deliberately batched:

        entry[] → changes[] → value → messages[] → {from, type, text.body}

    Two things that will bite:
      * DELIVERY/READ RECEIPTS arrive on the SAME webhook, as `value.statuses`
        with no `messages` key. Answering one would be absurd and would cost
        money, so anything without a real message returns None.
      * NON-TEXT messages (image, audio, sticker, location, button replies)
        have no `text.body`. We only handle text for now and ignore the rest
        rather than replying to something we did not read.
    """
    if not isinstance(payload, dict):
        return None
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            value = (change or {}).get("value") or {}
            # Receipts and status callbacks land here too — ignore them.
            messages = value.get("messages") or []
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") != "text":
                    continue  # image/audio/etc — not handled yet
                text = ((msg.get("text") or {}).get("body") or "").strip()
                sender = str(msg.get("from") or "").strip()
                if not text or not sender:
                    continue
                phone = "".join(ch for ch in sender if ch.isdigit())
                if not phone:
                    continue
                return IncomingMessage(
                    text=text,
                    user_key=f"wa:{phone}",
                    channel="whatsapp",
                    reply_to=phone,
                )
    return None


def parse_inbound(payload: dict) -> IncomingMessage | None:
    """Provider-agnostic entry point.

    Meta's payload is unmistakable (`object: whatsapp_business_account` with an
    `entry` list), so we detect rather than configure — one webhook URL keeps
    working if the provider changes underneath it.
    """
    if isinstance(payload, dict) and payload.get("entry") is not None:
        return parse_meta_inbound(payload)
    return parse_msg91_inbound(payload)


def parse_msg91_inbound(payload: dict) -> IncomingMessage | None:
    """Turn an MSG91 inbound-WhatsApp webhook payload into an IncomingMessage.

    Returns None for anything that isn't a user message — delivery receipts, read
    receipts and status callbacks all arrive on this same webhook and must be
    ignored (not answered).

    NOTE: MSG91's exact payload shape is confirmed against a live account. Until
    the number is live we accept several plausible shapes, so this keeps working
    whichever one it turns out to be. Narrow it once we see a real payload.
    """
    text = _pick(payload, [
        "content.text",
        "message.text.body",
        "message.content.text",
        "text.body",
        "content.body",
        "message.text",
        "text",
        "body",
    ])
    sender = _pick(payload, [
        "from",
        "mobile",
        "sender",
        "customer_number",
        "message.from",
        "contact.mobile",
        "waNumber",
    ])

    if not text or not sender:
        return None  # status callback / unsupported message type → ignore

    phone = "".join(ch for ch in sender if ch.isdigit())
    if not phone:
        return None

    return IncomingMessage(
        text=text,
        user_key=f"wa:{phone}",
        channel="whatsapp",
        reply_to=phone,
    )
