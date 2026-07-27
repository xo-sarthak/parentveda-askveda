"""
Two rules the app already decided, which this service must not quietly lose.

`lib/services/truth_hierarchy.dart` in the app repo ranks where an answer may
come from, and its own comments are unusually blunt:

    treatingClinician — "Beats everything, including a lab result we might read
                         differently."
    imaging           — "A dating scan lives here, which is why it outranks any
                         gestational age we calculate."
    populationEstimate— "'Most couples conceive within a year.' True of a
                         population, never a statement about this family — the
                         weakest claim we can make."

The service had none of that, and it showed. Before these rules existed, live
questions produced:

  * "my doctor told me to stop taking folic acid…"  → "You should continue taking
    folic acid" — the app overriding her clinician, against a stated product
    invariant ("the app must never contradict a user's own clinician").
  * "my scan said 8w5d but the app says 9w2d"       → "The app is right" —
    backwards; a dating scan outranks arithmetic from a last period.
  * 20 months of trying                              → "there's probably nothing
    wrong" — an unearned clinical judgment that can delay someone seeking help.

These are prompt rules, so they are pinned as prompt tests: fast, no network, and
they fail if someone tidies the wording away.
"""

import pytest

from app.prompt import SYSTEM_PROMPT, _STRUCTURED_FORMAT, describe_stage


# --- her clinician outranks us ---------------------------------------------

def test_the_clinician_rule_exists():
    p = SYSTEM_PROMPT.lower()
    assert "clinician" in p
    assert "outranks" in p


def test_the_model_is_told_never_to_override_her_doctor():
    p = SYSTEM_PROMPT.lower()
    assert "never tell her to override" in p


def test_all_eight_truth_levels_are_stated():
    """Mirrors TruthSource in the app — ALL of it. If the app's order changes,
    change it here too; the two must not drift.

    This test used to check five of the app's eight levels while calling itself
    "full", which is exactly how a gap reads as covered. `verifiedMedication` and
    `deviceData` were missing from the prompt, leaving two ordinary questions with
    no rule behind them: "my ring says day 12 but I felt it on day 14" and "my
    prescription says 200mg, the article says 400mg" — the latter central to IVF,
    where the cycle IS a medication schedule.
    """
    p = SYSTEM_PROMPT.lower()
    for level in (
        "treating clinician",      # TruthSource.treatingClinician
        "lab result",              # .laboratoryResult
        "scan",                    # .imaging
        "medication schedule",     # .verifiedMedication
        "observations",            # .userObservation
        "device",                  # .deviceData
        "parentveda worked out",   # .parentvedaDerived
        "population statistic",    # .populationEstimate
    ):
        assert level in p, f"missing from the stated ranking: {level!r}"


def test_a_dating_scan_beats_a_calculated_week():
    p = SYSTEM_PROMPT.lower()
    assert "dating scan beats a week we calculated" in p


def test_her_own_observation_beats_a_device():
    """The app puts userObservation above deviceData deliberately — "she knows the
    context a sensor cannot": that she slept badly, was unwell, or read it late."""
    p = SYSTEM_PROMPT.lower()
    assert "beats what a ring or a" in p


def test_a_prescription_outranks_our_content():
    """Central to IVF, where the whole cycle is a medication schedule."""
    p = SYSTEM_PROMPT.lower()
    assert "confirmed against a prescription" in p


# --- a population statistic is never about her ------------------------------

def test_the_population_rule_exists():
    p = SYSTEM_PROMPT.lower()
    assert "population statistic is never a statement about her" in p


@pytest.mark.parametrize("banned", ["odds", "chance", "success rate", "timeline"])
def test_personal_prediction_is_explicitly_forbidden(banned):
    assert banned in SYSTEM_PROMPT.lower(), (
        f"the prompt no longer forbids stating a personal {banned}"
    )


def test_the_actual_phrases_that_leaked_are_named():
    """Counter-examples earn their place: the polite version of this rule did not
    hold, and these are the exact sentences the model produced."""
    p = SYSTEM_PROMPT.lower()
    for phrase in ("the odds are in your favour", "you've only been trying",
                   "there's probably nothing wrong"):
        assert phrase in p, f"lost the counter-example: {phrase!r}"


def test_judging_whether_anything_is_wrong_is_routed_to_a_doctor():
    """The failure mode is not just a wrong number — it is reassurance that
    delays care."""
    p = SYSTEM_PROMPT.lower()
    assert "belongs to a doctor" in p


# --- the MEANING section must personalise without predicting ---------------

def test_meaning_still_asks_for_personalisation():
    assert "for HER" in _STRUCTURED_FORMAT


def test_meaning_may_not_become_a_prediction():
    """This label's whole job is to personalise, which is exactly why it is the
    place a population statistic turns into a personal forecast."""
    f = _STRUCTURED_FORMAT.lower()
    assert "not a prediction" in f
    for banned in ("no odds", "no chance", "no success rate", "no timeline"):
        assert banned in f, f"MEANING no longer forbids: {banned!r}"
    assert "no reassurance that nothing is wrong" in f


# --- pregnancy framing ------------------------------------------------------

def test_the_pregnancy_week_is_flagged_as_calculated_not_measured():
    note = describe_stage(week=9) or ""
    low = note.lower()
    assert "calculated" in low
    assert "dating scan" in low
    assert "more accurate" in low


def test_the_model_is_told_not_to_defend_the_app_s_own_number():
    note = (describe_stage(week=9) or "").lower()
    assert "rather than defending the app" in note


def test_the_other_stages_are_untouched():
    assert "NO LONGER PREGNANT" in (describe_stage(child_age_months=4) or "")
    assert describe_stage() is None
