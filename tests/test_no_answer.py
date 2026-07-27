"""
Gap detection — the trigger for the whole content flywheel.

This one has bitten us three separate times, always the same way: the model is
asked to emit a `NO_ANSWER` sentinel when our content can't answer, and it
obeys perhaps half the time. The rest of the time it invents a fresh way to say
"I couldn't find it" — and every phrasing we fail to recognise means:

  * the content gap is never logged (so it never reaches the editorial list), and
  * the trusted-web fallback never fires (so she is dead-ended for no reason).

Each phrasing below was produced by the live model. Treat the list as a record of
observed behaviour, not a guess.
"""

import pytest

from app.answer import _is_no_answer

OBSERVED_DECLINES = [
    "NO_ANSWER",
    # buried after a sentence of preamble
    "At around week 30 you'll have had a growth scan.\n\nNO_ANSWER",
    # prose forms — the sentinel skipped entirely
    "The content provided doesn't mention obstetric cholestasis.",
    "Unfortunately the content provided doesn't cover that.",
    "I couldn't find information on that in the provided content.",
    "I don't have information about stroller brands in the content provided.",
    "There is no information about that in the content.",
    "That is not in the provided content.",
]


@pytest.mark.parametrize("text", OBSERVED_DECLINES)
def test_every_observed_decline_is_detected(text):
    assert _is_no_answer(text), f"undetected decline → gap unlogged, web fallback skipped: {text!r}"


REAL_ANSWERS = [
    "At the anatomy scan, the sonographer checks your baby's growth and organs.",
    "Ripe papaya in small amounts is generally considered fine.",
    "Most babies are ready for solids at around 6 months.",
    "Your fertile window is roughly six days long.",
    # The tricky one: a genuine answer that happens to contain "doesn't mention".
    # Scoping the prose check to sentences about OUR content is what saves this.
    "Your report doesn't mention any abnormality, which is reassuring.",
]


@pytest.mark.parametrize("text", REAL_ANSWERS)
def test_real_answers_are_not_mistaken_for_declines(text):
    """A false positive here throws away a perfectly good answer and burns a
    web-fallback call instead."""
    assert not _is_no_answer(text), f"good answer misread as a decline: {text!r}"


def test_empty_and_none_are_safe():
    assert not _is_no_answer("")
    assert not _is_no_answer(None)


def test_detection_is_case_insensitive():
    assert _is_no_answer("no_answer")
    assert _is_no_answer("The content DOESN'T MENTION that.".lower())
