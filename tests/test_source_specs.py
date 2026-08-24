"""
Every content source must actually produce text.

WHY THIS FILE EXISTS. Until now every source kept its prose in one `body`
column, and `_chunk_rows_for` read `row["body"]` directly. The app's content
tables (migrations 0047-0049) do not have one: a recipe's teaching is spread
across `why`, `steps` and `mistakes`, and a read's whole article lives inside a
`sections` jsonb blob.

The failure mode of getting that wrong is the dangerous kind: `chunk_text(None)`
returns nothing, the ingest reports "0 chunks" for that table among five others,
and Ask Veda simply never knows the content exists. Nothing raises. The only
symptom is Veda saying "I don't know" about an article we published ourselves —
weeks later, to a parent, and blamed on the model.

So these tests assert the boring thing: given a realistic row, is there text.
"""

import pytest

from ingest.ingest import SOURCE_SPECS


ROWS = {
    "recipes": {
        "id": "r1", "domain": "parenting", "category": "Porridge",
        "title": "Ragi porridge", "subtitle": "First iron-rich meal",
        "age_tag": "6-8 mo", "highlight": "Iron + calcium",
        "why": "Ragi carries more calcium than most first grains.",
        "healthier_note": "Skip the sugar; mash a banana in instead.",
        "ingredients": ["2 tbsp ragi flour", "1 cup water"],
        "steps": ["Dry roast the flour.", "Whisk into water off the heat."],
        "storage": ["Best fresh."],
        "mistakes": ["Adding flour to hot water makes it lumpy."],
        "nutrients": [{"name": "Calcium", "amount": "344 mg", "note": "per 100g"}],
    },
    "reads": {
        "id": "d1", "domain": "parenting", "collection": "sleep",
        "title": "Why the 4-month sleep regression is not a regression",
        "teaser": "It is a permanent change in how sleep is organised.",
        "why_today": "Your baby is around four months.",
        "evidence": "Consistent across paediatric sleep literature.",
        "sections": [
            {
                "heading": "What actually changed",
                "paragraphs": ["Sleep cycles mature and become adult-like."],
                "tip": {"title": "Try this", "body": "Put down drowsy, not asleep."},
                "mythFact": {
                    "myth": "You have spoiled the baby.",
                    "fact": "Sleep architecture changed. Nothing you did caused it.",
                },
            }
        ],
    },
    "products": {
        "id": "p1", "domain": "parenting", "category": "Sleep",
        "name": "Hush White Noise", "brand": "Acme", "price_inr": 2499,
        "best_for": "Light sleepers in noisy flats.",
        "summary": "Steady output, simple controls.",
        "pros": ["No bright LEDs", "Runs on USB-C"],
        "cons": ["No volume lock", "Fabric cover traps dust"],
        "specs": {"Weight": "310 g"},
    },
    "articles": {"id": "a1", "title": "Week 20", "body": "Halfway."},
    "content_posts": {"id": "c1", "title": "Papaya", "body": "Ripe is fine."},
    "veda_knowledge": {"id": "k1", "title": "Ectopic", "body": "Seek care."},
}


def test_every_registered_source_has_a_sample_row():
    """A source added to SOURCE_SPECS without a row here is a source nobody
    checked produces text. Failing loudly beats an untested entry."""
    assert set(SOURCE_SPECS) == set(ROWS), (
        "SOURCE_SPECS and this file have drifted: "
        f"{set(SOURCE_SPECS) ^ set(ROWS)}"
    )


@pytest.mark.parametrize("table", sorted(SOURCE_SPECS))
def test_source_produces_text(table):
    _, _, text_fn = SOURCE_SPECS[table]
    text = text_fn(ROWS[table])
    assert text and text.strip(), f"{table} flattened to nothing"


@pytest.mark.parametrize("table", sorted(SOURCE_SPECS))
def test_source_survives_an_empty_row(table):
    """A draft row with almost nothing in it must not raise. Ingest walks every
    published row in one pass — one exception loses the whole batch, including
    the content that was fine."""
    _, _, text_fn = SOURCE_SPECS[table]
    assert isinstance(text_fn({}), str)
    assert isinstance(text_fn({"sections": None, "pros": None, "steps": None}), str)


def test_selected_columns_cover_what_the_text_builder_reads():
    """The select list and the flattener are two halves of one decision. If a
    builder reads a column the spec does not fetch, the key is simply absent at
    runtime and that part of the article vanishes -- silently, and only in
    production, because a hand-made row in a test has every key."""
    for table, (cols, _, text_fn) in SOURCE_SPECS.items():
        selected = {c.strip() for c in cols.split(",")}
        row = ROWS[table]
        used = {k for k in row if k != "id"}
        missing = used - selected
        assert not missing, (
            f"{table}: the sample row uses {sorted(missing)}, which "
            f"SOURCE_SPECS does not select. Either add the columns or stop "
            f"reading them."
        )


def test_products_never_ground_an_answer_on_the_good_half_only():
    """THE ONE THAT MATTERS. A catalogue that feeds Ask Veda pros without cons
    is an advert wearing a recommendation's clothes -- a real defect once, in
    the app's parenting corpus, fixed alongside migration 0049.

    Trimming this for token budget is exactly the change that would reintroduce
    it, so the invariant is pinned here rather than trusted to a comment."""
    _, _, text_fn = SOURCE_SPECS["products"]
    text = text_fn(ROWS["products"])
    for con in ROWS["products"]["cons"]:
        assert con in text, f"cons are missing from the embedded text: {con}"
    for pro in ROWS["products"]["pros"]:
        assert pro in text


def test_a_reads_myth_fact_card_reaches_the_index():
    """Myth-vs-fact is the block a parent most needs corrected, and it is
    nested two levels inside `sections`. A flattener that walks paragraphs and
    stops would drop it without any sign."""
    _, _, text_fn = SOURCE_SPECS["reads"]
    text = text_fn(ROWS["reads"])
    assert "You have spoiled the baby." in text
    assert "Sleep architecture changed." in text
    assert "Put down drowsy, not asleep." in text
