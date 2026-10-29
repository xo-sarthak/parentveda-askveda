"""
window_source: what placed her fertile days this cycle (2026-10-29, app T2).

The app's fertile window follows a body signal over the calendar. Before this
field the service only had her cycle day, so an answer about timing could
recount her window from an average she had already overruled with a test.

Three things are pinned here:

1. The note says it, and only for a value we know.
2. A fully medicated cycle drops it: her signals are noise there.
3. It is in the TTC cache key when present (an answer saying "your days follow
   your test" must never reach someone with no test), and absent otherwise, so
   everyone who sent nothing keeps their old key and its cached answers.
"""

from app import cache
from app.prompt import WINDOW_SOURCES, clean_window_source, describe_ttc_stage


def test_an_lh_test_reaches_the_note():
    note = describe_ttc_stage("tryingTogether", "natural", 4, 14,
                              timing_ownership="parentveda",
                              window_source="lh")
    assert "positive ovulation (LH) test" in note
    assert "Do NOT recount her window" in note


def test_a_temperature_rise_reaches_the_note():
    note = describe_ttc_stage("tryingTogether", "natural", 4, 20,
                              timing_ownership="parentveda",
                              window_source="temperature")
    assert "basal temperature" in note


def test_no_source_adds_nothing():
    plain = describe_ttc_stage("tryingTogether", "natural", 4, 14,
                               timing_ownership="parentveda")
    for line in WINDOW_SOURCES.values():
        assert line not in plain


def test_an_unknown_value_is_dropped():
    assert clean_window_source("calendar") is None
    note = describe_ttc_stage("tryingTogether", "natural", 4, 14,
                              timing_ownership="parentveda",
                              window_source="calendar")
    for line in WINDOW_SOURCES.values():
        assert line not in note


def test_a_medicated_cycle_drops_it():
    assert clean_window_source("lh", "clinic_controlled") is None
    note = describe_ttc_stage("tryingTogether", "ivf", 9,
                              timing_ownership="clinic_controlled",
                              window_source="lh")
    assert WINDOW_SOURCES["lh"] not in note


def test_a_guided_cycle_keeps_it():
    """An LH-timed IUI: her strip is exactly what the clinic acts on."""
    assert clean_window_source("lh", "clinic_guided") == "lh"


def test_the_cache_key_splits_only_when_sent():
    base = cache.stage_key_for(stage="trying", chapter="tryingTogether",
                               ttc_path="natural", timing_ownership="parentveda")
    lh = cache.stage_key_for(stage="trying", chapter="tryingTogether",
                             ttc_path="natural", timing_ownership="parentveda",
                             window_source="lh")
    assert lh != base
    assert lh == base + ":wlh"
    assert not base.endswith(("wlh", "wtemperature")), \
        "a caller that sent nothing must keep the old key"
