"""
Safety rails — the tests that matter most in this repo.

Red-flag routing is the one place where being wrong has a physical consequence.
An ectopic pregnancy is time-critical: a positive test plus one-sided or
shoulder-tip pain needs SAME-DAY care, not a generic "see your doctor sometime".
These phrase lists look like tidy-up-able clutter to a future refactor, and if
they go quiet nothing LOOKS broken — the assistant just answers normally. Hence
tests.

Pure functions only: no network, no database.
"""

import pytest

from app import guardrails


def _msg(question: str) -> str | None:
    return guardrails.red_flag_response(question)


# --- ectopic: the highest-stakes rule in the product ------------------------

ECTOPIC_QUESTIONS = [
    "positive test and one-sided pain",
    "I have shoulder tip pain",
    "shoulder-tip pain and I just found out I'm pregnant",
    "tested positive and now bleeding and dizzy",
    "positive pregnancy test with pain on one side",
    "could this be an ectopic pregnancy?",
]


@pytest.mark.parametrize("q", ECTOPIC_QUESTIONS)
def test_possible_ectopic_is_routed_same_day(q):
    m = _msg(q)
    assert m is not None, f"no red flag raised for: {q!r}"
    # It must say TODAY. The generic "contact your doctor" wording is not enough
    # here — that is the whole reason this rule has its own message.
    assert "today" in m.lower(), f"ectopic routing lost its same-day urgency: {m!r}"


def test_ectopic_beats_the_generic_bleeding_rule():
    """'bleeding' alone is a general red flag; with a positive test + dizziness it
    must escalate to the ectopic message, not fall through to the generic one."""
    specific = _msg("tested positive and now bleeding and dizzy")
    generic = _msg("bleeding between periods")
    assert specific != generic
    assert "today" in (specific or "").lower()


# --- OHSS: the IVF-cycle emergency ------------------------------------------

@pytest.mark.parametrize("q", [
    "I think I have OHSS",
    "bloated and breathless after ivf",
    "sudden weight gain after ivf",
    "ovarian hyperstimulation symptoms",
])
def test_ohss_routes_to_the_treating_clinic(q):
    m = _msg(q)
    assert m is not None, f"no red flag raised for: {q!r}"
    assert "clinic" in m.lower(), f"OHSS should route to the treating clinic: {m!r}"


# --- general TTC + the pre-existing pregnancy/parenting rules ---------------

@pytest.mark.parametrize("q", [
    "severe pelvic pain",
    "bleeding between periods",
    "my periods have stopped",
    "I haven't had a period for months",
])
def test_general_ttc_concerns_still_route_to_a_doctor(q):
    assert _msg(q) is not None


@pytest.mark.parametrize("q", [
    "heavy bleeding at week 12",
    "my baby is not moving",
    "severe headache and blurred vision",
])
def test_the_existing_pregnancy_rules_were_not_broken_by_ttc(q):
    """The TTC rules were added alongside these, not instead of them."""
    assert _msg(q) is not None


def test_red_flag_messages_stay_calm():
    """Product rule: route to care without alarm styling. No shouting, no
    'EMERGENCY', no exclamation marks."""
    for q in ECTOPIC_QUESTIONS + ["I think I have OHSS", "severe pelvic pain"]:
        m = _msg(q) or ""
        assert "!" not in m, f"alarm punctuation in: {m!r}"
        assert not any(w in m for w in ("EMERGENCY", "URGENT", "WARNING"))


# --- the other half: ordinary questions must NOT be flagged ----------------
# A red flag short-circuits RAG entirely, so a false positive means a normal
# question gets a doctor note instead of an answer.

@pytest.mark.parametrize("q", [
    "when should we try this cycle?",
    "what is a follicular study?",
    "can I drink coffee while trying to conceive?",
    "when is my fertile window?",
    "what is an AMH test?",
    "how long does implantation take?",
    "what should I eat in the second trimester?",
    "when should I start solids?",
])
def test_ordinary_questions_are_not_flagged(q):
    assert _msg(q) is None, f"false positive red flag on: {q!r}"
