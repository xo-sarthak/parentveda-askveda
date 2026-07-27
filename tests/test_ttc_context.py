"""
TTC stage context — cache bucketing and answer framing.

Two rules here are easy to erode and expensive to lose:

1. **cycle_day must never enter the cache key.** It would split every question
   28 ways and destroy the hit rate. Someone "improving personalisation" would
   add it in good faith and only a cost graph would ever notice.
2. **A TTC user is NOT pregnant.** The framing note is the only thing stopping
   the model inheriting pregnancy register from retrieved pregnancy content —
   which it demonstrably does without it (that was a real bug, caught live: a
   woman 26 months into IVF was told her symptom was normal "during the early
   stages of pregnancy").
"""

import pytest

from app import cache
from app.prompt import build_full_messages, describe_ttc_stage, parse_structured


# --- cache bucketing --------------------------------------------------------

def test_ttc_buckets_on_chapter_and_path():
    a = cache.stage_key_for(stage="trying", chapter="theWaitingDays", ttc_path="natural")
    b = cache.stage_key_for(stage="trying", chapter="theWaitingDays", ttc_path="ivf")
    c = cache.stage_key_for(stage="trying", chapter="preparingTogether", ttc_path="natural")
    assert a != b, "path must split the bucket (IVF reads differently to natural)"
    assert a != c, "chapter must split the bucket"


def test_cycle_day_is_deliberately_not_in_the_cache_key():
    """Adding cycle_day here would give a 1-in-28 hit rate. If this test ever
    fails, read the comment in cache.stage_key_for before 'fixing' it."""
    keys = {
        cache.stage_key_for(
            stage="trying", chapter="theWaitingDays", ttc_path="natural",
        )
        for _ in range(3)
    }
    assert len(keys) == 1
    # And the signature must not have grown a cycle-day parameter.
    import inspect
    assert "cycle_day" not in inspect.signature(cache.stage_key_for).parameters


def test_ttc_key_never_collides_with_pregnancy_or_parenting():
    ttc = cache.stage_key_for(stage="trying", chapter="tryingTogether", ttc_path="natural")
    preg = cache.stage_key_for(week=24)
    parent = cache.stage_key_for(child_age_months=3)
    assert len({ttc, preg, parent}) == 3


def test_pregnancy_and_parenting_bucketing_still_works():
    assert cache.stage_key_for(week=8) != cache.stage_key_for(week=30)
    assert cache.stage_key_for(child_age_months=2) != cache.stage_key_for(child_age_months=9)


# --- timing ownership -------------------------------------------------------
# WHO owns the cycle's timing is more decisive than which treatment it is: the
# same `ivf` can be a natural-cycle transfer (her LH still matters) or a fully
# medicated one (her signals are noise, the wait ends in a beta blood test).

def test_ownership_splits_the_cache_bucket_even_on_the_same_path():
    """Sharing a cached answer between a guided and a controlled IVF cycle would
    tell one of them something untrue about her own body."""
    guided = cache.stage_key_for(stage="trying", chapter="theWaitingDays",
                                 ttc_path="ivf", timing_ownership="clinic_guided")
    controlled = cache.stage_key_for(stage="trying", chapter="theWaitingDays",
                                     ttc_path="ivf", timing_ownership="clinic_controlled")
    assert guided != controlled


def test_a_medicated_cycle_forbids_fertile_window_talk():
    note = describe_ttc_stage("theWaitingDays", "ivf", 18,
                              timing_ownership="clinic_controlled").lower()
    assert "do not talk about a fertile window" in note
    assert "beta blood test" in note


def test_a_medicated_cycle_suppresses_the_cycle_day():
    """Day 22 of a medicated cycle is not the same thing as day 22 of her own —
    quoting it back invites exactly the wrong inference."""
    note = describe_ttc_stage("theWaitingDays", "ivf", 18, 22,
                              timing_ownership="clinic_controlled")
    assert "day 22" not in note
    # ...but it is meaningful when her own body sets the timing.
    own = describe_ttc_stage("theWaitingDays", "natural", 4, 22,
                             timing_ownership="parentveda")
    assert "day 22" in own


def test_a_guided_cycle_takes_her_own_signals_seriously():
    """On a natural-cycle FET or an LH-timed IUI her strips are precisely what the
    clinic acts on — dismissing them would be wrong."""
    note = describe_ttc_stage("tryingTogether", "iui", 9,
                              timing_ownership="clinic_guided").lower()
    assert "lh" in note
    assert "do not talk about a fertile window" not in note


def test_ownership_survives_an_unknown_value():
    note = describe_ttc_stage("tryingTogether", "ivf", 6, timing_ownership="something_new")
    assert "NOT pregnant" in note  # still framed, just without the ownership note


# --- framing ----------------------------------------------------------------

def test_ttc_framing_says_she_is_not_pregnant():
    note = describe_ttc_stage("theWaitingDays", "natural", 4, 22)
    assert "NOT pregnant" in note


def test_ttc_framing_forbids_pregnancy_register():
    """The counter-examples are load-bearing — a polite instruction alone lost to
    the register of the retrieved pregnancy content."""
    note = describe_ttc_stage("tryingTogether", "natural", 3)
    lowered = note.lower()
    for phrase in ("during your pregnancy", "your baby"):
        assert phrase in lowered, f"lost the counter-example: {phrase!r}"


def test_long_trying_changes_the_register():
    """'We started last month' and 'we've been trying two years' must never read
    the same. Past a year the model is told explicitly not to be breezy."""
    short = describe_ttc_stage("tryingTogether", "natural", 2)
    long = describe_ttc_stage("tryingTogether", "ivf", 26)
    assert "do not be breezy" in long.lower()
    assert "do not be breezy" not in short.lower()


def test_path_changes_the_vocabulary():
    assert "IVF" in describe_ttc_stage("theWaitingDays", "ivf", 12)
    assert "IUI" in describe_ttc_stage("theWaitingDays", "iui", 12)


def test_framing_survives_unknown_values():
    """Chapter/path come off the wire; an unknown value must not explode."""
    note = describe_ttc_stage("somethingNew", "unknownPath", None, None)
    assert "NOT pregnant" in note


def test_the_stage_note_reaches_the_prompt():
    note = describe_ttc_stage("theWaitingDays", "ivf", 26)
    msgs = build_full_messages("is this normal?", [], stage_note=note)
    assert any("NOT pregnant" in m["content"] for m in msgs)


# --- structured parsing (bitten us before) ---------------------------------

def test_parse_structured_reads_all_three_sections():
    p = parse_structured(
        "ANSWER: Yes, in moderation.\nMEANING: For you, that means a small cup.\n"
        "ACTIONS:\n- Keep it under 200mg\n- Ask your doctor if unsure"
    )
    assert p["answer"].startswith("Yes")
    assert "small cup" in p["meaning"]
    assert len(p["actions"]) == 2


def test_empty_meaning_does_not_swallow_the_actions_label():
    """A regression: `\\s*` after MEANING ate the newline, so an empty MEANING
    captured the literal text 'ACTIONS:'."""
    p = parse_structured("ANSWER: Something.\nMEANING:\nACTIONS:\n- Do a thing")
    assert "ACTIONS" not in p["meaning"]
    assert p["actions"] == ["Do a thing"]


def test_a_source_line_is_not_treated_as_an_action():
    p = parse_structured(
        "ANSWER: A.\nMEANING: B.\nACTIONS:\n- Call your doctor\n- Source: [1] NHS"
    )
    assert p["actions"] == ["Call your doctor"]


def test_unlabelled_output_falls_back_to_the_whole_text():
    """Small models ignore the format sometimes; we must still show something."""
    p = parse_structured("just a plain answer with no labels")
    assert p["answer"] == "just a plain answer with no labels"
    assert p["actions"] == []
