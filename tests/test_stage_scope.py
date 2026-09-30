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


@pytest.mark.parametrize("stage", [None, "", "pregnancy", "parenting"])
def test_other_stages_are_unscoped_until_their_own_pass(stage):
    assert scope_domain(stage, None) is None


def test_ttc_cache_key_is_new_so_unscoped_answers_are_never_served():
    key = stage_key_for(stage="trying", chapter="waiting", ttc_path="natural")
    assert key.startswith("ttc:s1:")
