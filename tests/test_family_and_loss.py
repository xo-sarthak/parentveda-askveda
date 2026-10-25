"""
Two app fields (2026-10-25, STAGE-TRANSITIONS R3 and F8 in the app repo).

`pregnancy_ended`: after she tells the app of a loss, the app sends no week,
and the answer must not speak of one; its cache entries are its own, so a
week's cached answer (a baby growing) is never served to her.

`family_stage`: on the parenting side, her own stage when it is not
parenting. The child's age alone made the note say "she is no longer
pregnant", untrue for a mother who is pregnant again, so the note and the
cache both tell the cases apart.
"""

from app.cache import stage_key_for
from app.prompt import describe_stage


# ---- after a loss ------------------------------------------------------------

def test_after_a_loss_the_note_has_no_week_and_no_baby_growing():
    note = describe_stage(None, None, None, pregnancy_ended=True)
    assert note is not None
    assert "pregnancy has ended" in note
    assert "week" not in note.replace("pregnancy weeks", "")
    assert "never suggest" in note


def test_the_loss_wins_over_a_stray_week():
    # An older build might still send a week; the loss decides.
    note = describe_stage(14, None, None, pregnancy_ended=True)
    assert "pregnancy has ended" in note
    assert "week 14" not in note


def test_after_a_loss_answers_have_their_own_cache_bucket():
    loss = stage_key_for(stage="pregnancy", pregnancy_ended=True)
    assert loss == "pl:s1"
    assert loss != stage_key_for(14, stage="pregnancy")
    # Even if a week arrives with it.
    assert stage_key_for(14, stage="pregnancy", pregnancy_ended=True) == "pl:s1"


def test_without_the_flag_pregnancy_is_unchanged():
    assert stage_key_for(14, stage="pregnancy") == "pw14:s2"
    assert "week 14" in describe_stage(14, None, None)


# ---- a parent who is pregnant again, or trying again ---------------------------

def test_pregnant_again_is_never_told_she_is_not_pregnant():
    note = describe_stage(None, None, 18, family_stage="pregnancy")
    assert "NO LONGER PREGNANT" not in note
    assert "pregnant again" in note
    assert "18 month(s) old" in note


def test_trying_again_says_so_and_assumes_nothing():
    note = describe_stage(None, None, 18, family_stage="trying")
    assert "NO LONGER PREGNANT" not in note
    assert "trying for another baby" in note


def test_a_parent_who_is_neither_keeps_the_old_note():
    note = describe_stage(None, None, 18)
    assert "NO LONGER PREGNANT" in note
    assert describe_stage(None, None, 18, family_stage=None) == note


def test_the_three_parent_cases_never_share_a_cache_entry():
    plain = stage_key_for(child_age_months=18, stage="parenting")
    again = stage_key_for(child_age_months=18, stage="parenting", family_stage="pregnancy")
    trying = stage_key_for(child_age_months=18, stage="parenting", family_stage="trying")
    assert plain == "cm18:s1"
    assert len({plain, again, trying}) == 3
