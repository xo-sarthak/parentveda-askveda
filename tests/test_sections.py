"""
The 7-section feed: routing, the relevance floor, and the bilingual twins.

The bilingual decision (each item stored twice — an English doc and a `_hi`
twin, both embedded so a Hinglish question can retrieve Hinglish) created two
failure modes that are invisible in the data and only show up on screen:

  * the SAME item appearing twice, once per language, because the twins are
    different rows;
  * a Hinglish card title shown to an English reader.

Both are fixed by dedup-on-base-doc-id and the `lang` filter. Neither is
self-evident from reading the code, so both are pinned here.
"""

from app import sections
from app.config import settings


def _chunk(doc_id, kind, sim, title="T", src="veda_knowledge", sid=None):
    return {
        "doc_id": doc_id,
        "category": kind,
        "similarity": sim,
        "title": title,
        "source_table": src,
        "source_id": sid or doc_id,
        "chunk_text": "body text",
    }


# --- kind → section routing -------------------------------------------------

def test_kinds_route_to_the_right_sections():
    rows = [
        _chunk("ttcinsight_a", "ttcinsight", 0.9),
        _chunk("ttcprod_b", "product", 0.9),
        _chunk("ttcoffer_c", "service", 0.9),
        _chunk("vid_d", "video", 0.9),
    ]
    s = sections.build_sections(rows)
    assert [i["doc_id"] for i in s["content"]] == ["ttcinsight_a"]
    assert [i["doc_id"] for i in s["products"]] == ["ttcprod_b"]
    assert [i["doc_id"] for i in s["services"]] == ["ttcoffer_c"]
    assert [i["doc_id"] for i in s["videos"]] == ["vid_d"]


def test_ttc_offerings_land_in_services_not_content():
    """TTC offerings are real bookable Offerings — they belong in S7."""
    s = sections.build_sections([_chunk("ttcoffer_gynae", "service", 0.9)])
    assert len(s["services"]) == 1 and not s["content"]


def test_every_section_key_is_always_present():
    """The app turns an empty list into 'Coming soon'; a MISSING key would crash
    it. Every response must carry all four."""
    s = sections.build_sections([])
    assert set(s) == {"content", "videos", "products", "services"}
    assert all(v == [] for v in s.values())


# --- the relevance floor (the precision knob) ------------------------------

def test_the_relevance_floor_drops_weak_matches():
    floor = settings.section_min_similarity
    rows = [
        _chunk("good", "ttcinsight", floor + 0.1),
        _chunk("weak", "ttcinsight", floor - 0.1),
    ]
    ids = [i["doc_id"] for i in sections.build_sections(rows)["content"]]
    assert ids == ["good"]


def test_sections_are_capped():
    rows = [_chunk(f"d{i}", "ttcinsight", 0.9) for i in range(20)]
    assert len(sections.build_sections(rows)["content"]) <= settings.section_max_items


# --- bilingual twins --------------------------------------------------------

def test_a_twin_pair_shows_once_not_twice():
    """The En and Hi docs are different rows, so keying on the row id would show
    the same offering twice — once in each language."""
    rows = [
        _chunk("ttcoffer_gynae", "service", 0.90, title="Gynaecologist consultation"),
        _chunk("ttcoffer_gynae_hi", "service", 0.88, title="Gynaecologist se consultation"),
    ]
    s = sections.build_sections(rows, lang="hi")
    assert len(s["services"]) == 1, "the same offering appeared twice"


def test_the_higher_scoring_twin_wins():
    """Results are similarity-ordered, so the twin matching the question's own
    language naturally comes first."""
    rows = [
        _chunk("ttcinsight_amh_hi", "ttcinsight", 0.90, title="Hinglish"),
        _chunk("ttcinsight_amh", "ttcinsight", 0.70, title="English"),
    ]
    s = sections.build_sections(rows, lang="hi")
    assert s["content"][0]["title"] == "Hinglish"


def test_an_english_reader_never_sees_a_hinglish_card():
    rows = [
        _chunk("ttcinsight_amh_hi", "ttcinsight", 0.95, title="Hinglish"),
        _chunk("ttcinsight_amh", "ttcinsight", 0.70, title="English"),
    ]
    s = sections.build_sections(rows, lang="en")
    assert [i["title"] for i in s["content"]] == ["English"]


def test_english_is_the_default_when_no_language_is_given():
    rows = [_chunk("ttcinsight_x_hi", "ttcinsight", 0.95)]
    assert sections.build_sections(rows) == sections.build_sections(rows, lang="en")


def test_a_hinglish_reader_still_gets_english_only_content():
    """Most pregnancy/parenting content has no `_hi` twin — a Hinglish reader
    must still see it rather than an empty section."""
    rows = [_chunk("cani_papaya", "canI", 0.9, title="Papaya")]
    s = sections.build_sections(rows, lang="hi")
    assert [i["title"] for i in s["content"]] == ["Papaya"]


def test_items_carry_the_deep_link_identity():
    s = sections.build_sections([_chunk("ttctest_amh", "ttctest", 0.9)])
    item = s["content"][0]
    assert item["doc_id"] == "ttctest_amh"  # the app routes on this prefix
    assert item["kind"] == "ttctest"
