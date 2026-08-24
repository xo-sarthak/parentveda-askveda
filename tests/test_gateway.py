"""
The gateway: one internal message shape, whatever the provider sends.

This is what lets us test on Meta's free test number now and switch to MSG91
later without the brain noticing. Two failure modes are worth pinning, because
both are silent and both cost money:

  * Answering a DELIVERY RECEIPT. Receipts arrive on the same webhook URL as
    real messages. Treating one as a question would send a reply to a machine
    event, spend an LLM call, and look like the bot talking to itself.
  * Answering a message we did not read. An image, sticker or location has no
    text body. Replying anyway means confidently answering something unseen.
"""

import pytest

from app.gateway import parse_inbound, parse_meta_inbound, parse_msg91_inbound


def _meta(**value) -> dict:
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": value}]}]}


def _text_msg(body="hello", frm="919876543210") -> dict:
    return _meta(messaging_product="whatsapp",
                 messages=[{"from": frm, "id": "wamid.X", "type": "text",
                            "text": {"body": body}}])


# --- Meta Cloud API ---------------------------------------------------------

def test_a_meta_text_message_is_parsed():
    m = parse_meta_inbound(_text_msg("when should I start solids?"))
    assert m is not None
    assert m.text == "when should I start solids?"
    assert m.user_key == "wa:919876543210"
    assert m.reply_to == "919876543210"
    assert m.channel == "whatsapp"


def test_a_delivery_receipt_is_not_a_question():
    receipt = _meta(statuses=[{"id": "wamid.X", "status": "delivered",
                               "recipient_id": "919876543210"}])
    assert parse_meta_inbound(receipt) is None


@pytest.mark.parametrize("kind,extra", [
    ("image", {"image": {"id": "abc"}}),
    ("audio", {"audio": {"id": "abc"}}),
    ("location", {"location": {"latitude": 1, "longitude": 2}}),
    ("sticker", {"sticker": {"id": "abc"}}),
])
def test_non_text_messages_are_ignored_rather_than_guessed(kind, extra):
    payload = _meta(messages=[{"from": "919876543210", "type": kind, **extra}])
    assert parse_meta_inbound(payload) is None


def test_an_empty_body_is_ignored():
    assert parse_meta_inbound(_text_msg("   ")) is None


def test_a_phone_number_is_reduced_to_digits():
    m = parse_meta_inbound(_text_msg("hi", frm="+91 98765-43210"))
    assert m is not None and m.user_key == "wa:919876543210"


def test_meta_junk_payloads_do_not_explode():
    for junk in ({}, {"entry": []}, {"entry": [{}]},
                 {"entry": [{"changes": [{}]}]}, {"entry": [{"changes": [{"value": {}}]}]}):
        assert parse_meta_inbound(junk) is None


# --- provider detection -----------------------------------------------------

def test_a_meta_payload_routes_to_the_meta_parser():
    m = parse_inbound(_text_msg("can I eat papaya?"))
    assert m is not None and m.text == "can I eat papaya?"


def test_an_msg91_payload_still_works():
    """Adding Meta must not break the provider we will actually ship on."""
    m = parse_inbound({"from": "919876543210", "content": {"text": "hello"}})
    assert m is not None
    assert m.text == "hello"
    assert m.user_key == "wa:919876543210"


def test_msg91_receipts_are_still_ignored():
    assert parse_msg91_inbound({"type": "status", "status": "delivered"}) is None


# --- stage context ----------------------------------------------------------

def test_stage_fields_exist_but_are_unset_until_phone_linking():
    """WhatsApp gives a phone number, not an app profile. The fields are already
    plumbed through to answer(); they stay None until a phone can be resolved to
    a profile, at which point WhatsApp becomes as personalised as the app."""
    m = parse_inbound(_text_msg("suggest products for my child"))
    assert m is not None
    assert m.week is None and m.child_age_months is None and m.trimester is None
