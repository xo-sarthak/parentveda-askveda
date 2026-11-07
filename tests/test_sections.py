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


# --- recipes from the table (the flip, app migration 0161) --------------------

def test_a_recipe_chunk_is_a_recipe_card_with_the_dish_id():
    c = _chunk("ragipancake", "Porridge", 0.9, src="recipes", sid="uuid-1")
    item = sections.build_sections([c])["content"][0]
    assert item["kind"] == "recipe"
    assert item["doc_id"] == "ragipancake"


def test_other_chunks_keep_their_own_kind():
    c = _chunk("ppart_a", "read", 0.9)
    assert sections.build_sections([c])["content"][0]["kind"] == "read"


class _FakeTable:
    """Stands in for the Supabase client, and refuses a column the table lacks."""
    def __init__(self, rows, columns):
        self.rows, self.columns = rows, columns
    def select(self, cols):
        for c in [x.strip() for x in cols.split(",")]:
            assert c in self.columns, f"no column {c}"
        return self
    def in_(self, *_):
        return self
    def execute(self):
        return type("R", (), {"data": self.rows})()


def test_a_recipe_card_gets_its_full_text_though_the_table_has_no_body(monkeypatch):
    from ingest.ingest import SOURCE_SPECS
    row = {"id": "u1", "domain": "parenting", "category": "Porridge",
           "title": "Ragi porridge", "subtitle": "", "age_tag": "", "highlight": "",
           "why": "Ragi carries calcium.", "healthier_note": "",
           "ingredients": ["ragi"], "steps": ["Roast it."], "storage": [],
           "mistakes": [], "nutrients": []}
    cols = {c.strip() for c in SOURCE_SPECS["recipes"][0].split(",")}
    fake = type("S", (), {"table": lambda self, t: _FakeTable([row], cols)})()
    monkeypatch.setattr(sections, "supabase", fake)
    item = {"source_table": "recipes", "source_id": "u1", "snippet": "cut off"}
    sections.attach_bodies([item])
    assert "Roast it." in item["body"]
