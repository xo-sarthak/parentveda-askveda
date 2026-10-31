"""
Ask Veda answers inside the stage she is in (2026-09-30).

The user: on the trying-to-conceive side, Ask Veda searches and answers from
the trying-to-conceive side only. `scope_domain` decides the one corpus domain
retrieval filters on, and the cache key changed so no answer cached before the
scope (which could point at pregnancy content) is served again.
"""

import pytest

from app.answer import scope_domain
from app.cache import stage_key_for


@pytest.mark.parametrize("stage", ["trying", "TTC", "trying_to_conceive"])
def test_trying_to_conceive_is_scoped_to_its_own_content(stage):
    assert scope_domain(stage, None) == "trying"


def test_an_explicit_domain_still_wins():
    assert scope_domain("trying", "pregnancy") == "pregnancy"


@pytest.mark.parametrize("stage", [None, ""])
def test_no_stage_stays_unscoped(stage):
    assert scope_domain(stage, None) is None


# ---- pregnancy's own pass (2026-10-02) ---------------------------------------

@pytest.mark.parametrize("stage", ["pregnancy", "Pregnancy"])
def test_pregnancy_is_scoped_to_its_own_content(stage):
    assert scope_domain(stage, None) == "pregnancy"


def test_a_build_that_sends_only_a_week_stays_unscoped():
    # An older app build sends `week` and no `stage`: it must behave exactly as
    # it did, not be silently scoped by a field it never sent.
    assert scope_domain(None, None) is None


def test_pregnancy_cache_key_is_new_so_unscoped_answers_are_never_served():
    # ":s2" since 2026-10-13 (the FAQ Library + rule 10); ":s1" before it, and
    # the bare key before the stage scope. Each bump strands the older answers.
    assert stage_key_for(week=20, stage="pregnancy") == "pw20:s2"
    assert stage_key_for(trimester="second", stage="pregnancy") == "ptsecond:s2"
    assert stage_key_for(stage="pregnancy") == "p:s2"


def test_an_older_build_keeps_its_old_pregnancy_key():
    assert stage_key_for(week=20) == "pw20"
    assert stage_key_for(trimester="second") == "ptsecond"
    assert stage_key_for() == ""


def test_an_older_parenting_build_and_ttc_keep_their_keys():
    assert stage_key_for(child_age_months=4) == "cm4"
    assert stage_key_for(stage="trying", chapter="waiting",
                         ttc_path="natural").startswith("ttc:s1:")


def test_ttc_cache_key_is_new_so_unscoped_answers_are_never_served():
    key = stage_key_for(stage="trying", chapter="waiting", ttc_path="natural")
    assert key.startswith("ttc:s1:")


# ---- parenting's own pass (2026-10-10) ---------------------------------------

@pytest.mark.parametrize("stage", ["parenting", "Parenting"])
def test_parenting_is_scoped_to_its_own_content(stage):
    assert scope_domain(stage, None) == "parenting"


def test_parenting_cache_key_is_new_so_unscoped_answers_are_never_served():
    assert stage_key_for(child_age_months=4, stage="parenting") == "cm4:s1"
    assert stage_key_for(stage="parenting") == "c:s1"


# ---- her stage first, then the whole library (2026-10-31) --------------------

from app.answer import pick_results  # noqa: E402


def _r(sim, doc="d"):
    return [{"similarity": sim, "doc_id": doc}]


def test_her_stage_wins_when_it_can_carry_the_answer():
    res, widened = pick_results(_r(0.55, "ttc"), _r(0.58, "preg"), floor=0.30)
    assert (res[0]["doc_id"], widened) == ("ttc", False)


def test_a_clearly_better_match_elsewhere_is_used():
    # A TTC user asking a pregnancy question: hers barely matches, pregnancy's does.
    res, widened = pick_results(_r(0.34, "ttc"), _r(0.71, "preg"), floor=0.30)
    assert (res[0]["doc_id"], widened) == ("preg", True)


def test_nothing_of_hers_but_something_elsewhere_is_used():
    res, widened = pick_results([], _r(0.62, "preg"), floor=0.30)
    assert (res[0]["doc_id"], widened) == ("preg", True)


def test_nothing_anywhere_stays_hers_and_falls_to_the_web():
    res, widened = pick_results(_r(0.12, "ttc"), _r(0.20, "preg"), floor=0.30)
    assert (res[0]["doc_id"], widened) == ("ttc", False)
