"""
`previous_loss` (2026-10-26, STILL-OPEN §61.1 in the app repo).

She is pregnant again, or trying again, after a pregnancy loss she recorded in
the app. The answer keeps her stage (her week, her cycle) and adds gentle
framing on top: no congratulations, room for worry, never a probability, never
blame. Its cache entries are apart from the same stage's ordinary answers. It
never applies on the parenting side, and never while the pregnancy that ended
is the one on screen (`pregnancy_ended` frames that).
"""

from app.cache import stage_key_for
from app.prompt import describe_stage, describe_ttc_stage, with_previous_loss


def test_pregnant_after_a_loss_keeps_her_week_and_adds_the_line():
    base = describe_stage(9, None, None)
    note = with_previous_loss(base, previous_loss=True, is_ttc=False)
    assert note.startswith(base), "her week must stay in the note"
    assert "last pregnancy ended in a loss" in note
    assert "congratulations" in note and "Never give a chance" in note


def test_trying_after_a_loss_gets_its_own_line():
    base = describe_ttc_stage("theWaitingDays", "natural", 4, None)
    note = with_previous_loss(base, previous_loss=True, is_ttc=True)
    assert "trying again after a pregnancy loss" in note
    assert "doctor can advise on timing" in note


def test_nothing_changes_without_the_flag_or_on_the_parenting_side():
    base = describe_stage(9, None, None)
    assert with_previous_loss(base, previous_loss=None, is_ttc=False) == base
    child = describe_stage(None, None, 14)
    assert with_previous_loss(child, previous_loss=True, is_ttc=False,
                              child_age_months=14) == child


def test_the_pregnancy_that_ended_keeps_the_loss_note_alone():
    loss = describe_stage(None, None, None, pregnancy_ended=True)
    assert with_previous_loss(loss, previous_loss=True, is_ttc=False,
                              pregnancy_ended=True) == loss


def test_after_a_loss_answers_have_their_own_cache_entries():
    plain = stage_key_for(9, stage="pregnancy")
    after = stage_key_for(9, stage="pregnancy", previous_loss=True)
    assert after == plain + ":al"
    ttc = stage_key_for(stage="trying", chapter="theWaitingDays", ttc_path="natural")
    ttc_after = stage_key_for(stage="trying", chapter="theWaitingDays",
                              ttc_path="natural", previous_loss=True)
    assert ttc_after == ttc + ":al"


def test_the_flag_never_splits_the_loss_bucket_or_a_childs():
    assert stage_key_for(stage="pregnancy", pregnancy_ended=True,
                         previous_loss=True) == "pl:s1"
    assert stage_key_for(child_age_months=14, stage="parenting",
                         previous_loss=True) == stage_key_for(child_age_months=14,
                                                              stage="parenting")
