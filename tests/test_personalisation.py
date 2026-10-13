"""
Write for where she is (2026-10-13).

The user's FAQ Library joined pregnancy's knowledge as FACTS, not as canned
answers: "if the mother is in week 20, the answer needs to be personalized for
her… I don't want those templated answers that are same for everyone." The FAQs
are written by month, so the prompt has to tell the model to re-frame a month-one
fact for a mother at week thirty rather than repeat it, or refuse it.

These pin the words that carry that, the way test_truth_and_probability pins
rules 6 and 7. No network, no model.
"""

from app.cache import stage_key_for
from app.prompt import NO_ANSWER, SYSTEM_PROMPT, _STRUCTURED_FORMAT, describe_stage


def test_rule_ten_asks_for_her_point_not_a_leaflet():
    p = SYSTEM_PROMPT.lower()
    assert "make it hers" in p
    assert "generic leaflet" in p
    assert "already behind her" in p and "still ahead" in p


def test_written_for_content_is_reframed_not_refused():
    p = SYSTEM_PROMPT
    assert '"Written for"' in p
    assert "never answer as if she were at the content's stage" in p
    assert f"not a reason to emit `{NO_ANSWER}`" in p


def test_personal_never_becomes_a_prediction():
    # Rule 10 must not loosen rules 6 and 7.
    p = SYSTEM_PROMPT.lower()
    assert "not a prediction about her" in p
    assert "rules 6 and 7 still hold" in p


def test_the_answer_label_itself_asks_for_her_point():
    f = _STRUCTURED_FORMAT
    assert "written for where she is now" in f
    assert "not a generic" in f
    assert "for HER" in f  # the MEANING label keeps its personalisation


def test_the_week_carries_its_trimester():
    assert "first trimester" in describe_stage(week=6)
    assert "first trimester" in describe_stage(week=13)
    assert "second trimester" in describe_stage(week=14)
    assert "second trimester" in describe_stage(week=27)
    assert "third trimester" in describe_stage(week=28)
    assert "third trimester" in describe_stage(week=40)


def test_answers_cached_before_the_library_are_never_served():
    assert stage_key_for(week=6, stage="pregnancy").endswith(":s2")
    assert not stage_key_for(week=6, stage="pregnancy").endswith(":s1")


def test_rule_two_lets_other_stage_content_answer_when_its_facts_settle_it():
    # Rule 2 (answer only from content that DIRECTLY answers) is read before
    # rule 10; on 2026-10-13 a model refused "folic acid?" at week 28 although
    # the FAQ says "through at least the first trimester". The exception lives
    # in rule 2 itself, and so does its limit.
    p = SYSTEM_PROMPT
    assert "Content written for another stage of pregnancy" in p
    assert "DOES directly answer when its facts settle her question at her stage" in p
    assert "does NOT answer when the facts only hold" in p.replace("It does NOT", "does NOT")
