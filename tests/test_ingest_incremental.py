"""
The full ingest embeds only what changed and removes only what is gone.

WHY THIS FILE EXISTS. Until 2026-10-17 every run re-embedded every chunk
(8,856 of them, minutes at 95 °C on a laptop CPU) and never deleted anything, so
an unpublished recipe stayed answerable. `plan_changes` is the decision now, and
it is pure, so the cases that matter are pinned here without Supabase. The one
that matters most: a table that failed to read must lose nothing.
"""

from ingest.ingest import content_hash, plan_changes


def _k(table: str, sid: str, i: int = 0):
    return (table, sid, i)


def test_unchanged_run_does_nothing():
    produced = {_k("recipes", "a"): content_hash("ragi")}
    to_embed, stale = plan_changes(produced, dict(produced), {"recipes"})
    assert to_embed == [] and stale == []


def test_new_and_changed_chunks_embed():
    existing = {_k("recipes", "a"): content_hash("ragi")}
    produced = {
        _k("recipes", "a"): content_hash("ragi, now with banana"),  # changed
        _k("recipes", "b"): content_hash("khichdi"),                # new
    }
    to_embed, stale = plan_changes(produced, existing, {"recipes"})
    assert sorted(to_embed) == [_k("recipes", "a"), _k("recipes", "b")]
    assert stale == []


def test_a_row_written_before_hashes_re_embeds_once():
    produced = {_k("articles", "x"): content_hash("body")}
    to_embed, _ = plan_changes(produced, {_k("articles", "x"): None}, {"articles"})
    assert to_embed == [_k("articles", "x")]


def test_removed_content_and_a_shrunk_body_go_stale():
    existing = {
        _k("recipes", "soup"): content_hash("old soup"),      # recipe archived
        _k("reads", "r", 0): content_hash("part one"),
        _k("reads", "r", 1): content_hash("part two"),        # body shrank
    }
    produced = {_k("reads", "r", 0): content_hash("part one")}
    _, stale = plan_changes(produced, existing, {"recipes", "reads"})
    assert sorted(stale) == [_k("reads", "r", 1), _k("recipes", "soup")]


def test_an_unreadable_table_loses_nothing():
    # products failed to read this run, so it produced nothing. Its chunks must
    # NOT be read as removed.
    existing = {_k("products", "p"): content_hash("a pram")}
    _, stale = plan_changes({}, existing, readable={"recipes"})
    assert stale == []


def test_full_re_embeds_everything():
    produced = {_k("recipes", "a"): content_hash("ragi")}
    to_embed, _ = plan_changes(produced, dict(produced), {"recipes"}, full=True)
    assert to_embed == [_k("recipes", "a")]


def test_the_model_is_part_of_the_fingerprint(monkeypatch):
    from app.config import settings
    before = content_hash("ragi")
    monkeypatch.setattr(settings, "embed_model", "some/other-model")
    assert content_hash("ragi") != before
