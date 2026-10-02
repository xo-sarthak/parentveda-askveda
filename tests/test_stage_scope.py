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


@pytest.mark.parametrize("stage", [None, "", "parenting"])
def test_other_stages_are_unscoped_until_their_own_pass(stage):
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
    assert stage_key_for(week=20, stage="pregnancy") == "pw20:s1"
    assert stage_key_for(trimester="second", stage="pregnancy") == "ptsecond:s1"
    assert stage_key_for(stage="pregnancy") == "p:s1"


def test_an_older_build_keeps_its_old_pregnancy_key():
    assert stage_key_for(week=20) == "pw20"
    assert stage_key_for(trimester="second") == "ptsecond"
    assert stage_key_for() == ""


def test_parenting_and_ttc_keys_are_untouched():
    assert stage_key_for(child_age_months=4) == "cm4"
    assert stage_key_for(child_age_months=4, stage="parenting") == "cm4"
    assert stage_key_for(stage="trying", chapter="waiting",
                         ttc_path="natural").startswith("ttc:s1:")


def test_ttc_cache_key_is_new_so_unscoped_answers_are_never_served():
    key = stage_key_for(stage="trying", chapter="waiting", ttc_path="natural")
    assert key.startswith("ttc:s1:")
