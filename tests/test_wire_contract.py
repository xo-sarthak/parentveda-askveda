"""
The /ask wire body is a contract across TWO repos.

The app (`C:\\Projects\\parentveda`) builds the request in
`lib/services/remote/ask_veda_service.dart`; this service consumes it. Adding a
field to one side alone does nothing — and used to do nothing SILENTLY, because
Pydantic drops unknown fields without a murmur. That is exactly how
`timing_ownership` was sent by the app for days while this service discarded it:
no error, no 4xx, no log, and the framing it was meant to drive simply never
happened.

So the model now keeps unknowns and the route logs them. These tests keep that
alarm wired up.
"""

import inspect

from app.answer import answer
from app.channels.app_api import AskRequest


def test_an_unknown_field_is_reported_not_swallowed():
    body = AskRequest(question="x", some_future_field="v")
    assert body.unknown_fields() == ["some_future_field"], (
        "unknown fields are being dropped again — a one-sided contract change "
        "will go silent"
    )


def test_a_known_field_is_not_reported_as_unknown():
    body = AskRequest(
        question="x", stage="trying", chapter="theWaitingDays",
        ttc_path="ivf", timing_ownership="clinic_controlled",
        cycle_day=22, months_trying=18, lang="en",
    )
    assert body.unknown_fields() == []


def test_every_field_the_app_sends_is_understood_here():
    """The list mirrors the JSON keys in AskVedaService.ask(). If the app adds a
    key and this list is updated without adding the field, this fails — which is
    the point."""
    sent_by_app = {
        "question", "week", "trimester", "child_age_months",
        "stage", "chapter", "cycle_day", "ttc_path", "timing_ownership",
        "months_trying", "lang", "domain",
    }
    known = set(AskRequest.model_fields)
    missing = sent_by_app - known
    assert not missing, f"the app sends {missing}, which this service ignores"


def test_context_fields_reach_the_brain():
    """A field can be accepted by the model and still never be passed on — that
    would be just as silent. Check answer() actually takes each one."""
    params = set(inspect.signature(answer).parameters)
    for field in ("stage", "chapter", "cycle_day", "ttc_path",
                  "timing_ownership", "months_trying", "lang"):
        assert field in params, f"answer() cannot receive {field!r}"


def test_every_context_field_is_optional():
    """Stage context is framing, never a requirement — a caller that knows none
    of it (WhatsApp, today) must still get an answer."""
    body = AskRequest(question="just a question")
    assert body.stage is None and body.timing_ownership is None
    for name, field in AskRequest.model_fields.items():
        if name == "question":
            continue
        assert not field.is_required(), f"{name} must stay optional"
