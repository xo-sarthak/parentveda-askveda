"""
Section builder (the 7-section feed) — sections 4/6/7 (+ videos).

Sections 1–3 (answer / meaning / actions) come from the LLM. Sections 4–7 are
POINTERS — the relevant retrieved items, grouped by their `kind`, ranked by the
same semantic score. This is the "Google feed" part: one search, results grouped
by type, each above a relevance floor so nothing tangential leaks in.

  content  → More information (articles, reads, guides, Can-I, Garbh, …)
  videos   → Videos (kind='video' — none ingested yet → app shows "Coming soon")
  products → Products (kind='product')
  services → Services (kind='expert')

Every section is ALWAYS returned (possibly empty); the app turns an empty list
into a "Coming soon" card so the 7-section format never collapses.
"""

from app.config import settings
from app.db import supabase

_PRODUCT_KINDS = {"product"}
_SERVICE_KINDS = {"expert"}
_VIDEO_KINDS = {"video"}


def _section_for(kind: str | None) -> str:
    k = (kind or "").strip().lower()
    if k in _PRODUCT_KINDS:
        return "products"
    if k in _SERVICE_KINDS:
        return "services"
    if k in _VIDEO_KINDS:
        return "videos"
    return "content"  # everything informational


def enrich_doc_ids(results: list[dict]) -> list[dict]:
    """Attach the app's own `doc_id` (+ source_label/url) to veda_knowledge chunks.

    The chunk only stores the Supabase row id; the app deep-links by the VedaDoc id
    it already knows, so we look that up. Best-effort — a failure just means the app
    falls back to routing by (source_table, source_id)."""
    vk_ids = list({
        r.get("source_id") for r in results
        if r.get("source_table") == "veda_knowledge" and r.get("source_id")
    })
    info: dict[str, dict] = {}
    if vk_ids:
        try:
            rows = (
                supabase.table("veda_knowledge")
                .select("id, doc_id, source_label")
                .in_("id", vk_ids)
                .execute()
            ).data or []
            info = {row["id"]: row for row in rows}
        except Exception as e:  # pragma: no cover
            print(f"[sections.enrich_doc_ids] non-fatal: {e}")

    for r in results:
        if r.get("source_table") == "veda_knowledge":
            meta = info.get(r.get("source_id"), {})
            r["doc_id"] = meta.get("doc_id")
            if meta.get("source_label"):
                r["source_label"] = meta["source_label"]
    return results


def _item(r: dict) -> dict:
    """One pointer card — everything the app needs to display AND deep-link it."""
    return {
        # deep-link identity: prefer the app's doc_id, else the source row id
        "doc_id": r.get("doc_id") or r.get("source_id"),
        "source_table": r.get("source_table"),
        "source_id": r.get("source_id"),
        "kind": r.get("category"),
        "title": r.get("title"),
        "url": r.get("url"),
        "snippet": (r.get("chunk_text") or "").strip()[:140],
        "similarity": round(float(r.get("similarity") or 0), 3),
    }


def attach_bodies(items: list[dict]) -> None:
    """Fetch the FULL body for content items so tapping one opens the real article
    (not a snippet) in the app's reader. Grouped per source table; best-effort."""
    by_table: dict[str, list[str]] = {}
    for it in items:
        if it.get("source_table") and it.get("source_id"):
            by_table.setdefault(it["source_table"], []).append(it["source_id"])
    bodies: dict[tuple, str] = {}
    for table, ids in by_table.items():
        try:
            rows = supabase.table(table).select("id, body").in_("id", ids).execute().data or []
            for r in rows:
                bodies[(table, r["id"])] = r.get("body") or ""
        except Exception as e:  # pragma: no cover
            print(f"[sections.attach_bodies] non-fatal ({table}): {e}")
    for it in items:
        it["body"] = bodies.get((it.get("source_table"), it.get("source_id"))) or it.get("snippet", "")


def build_sections(results: list[dict]) -> dict:
    """Group retrieved chunks → the pointer sections (deduped, floored, capped)."""
    floor = settings.section_min_similarity
    cap = settings.section_max_items
    out: dict[str, list] = {"content": [], "videos": [], "products": [], "services": []}
    seen: dict[str, set] = {k: set() for k in out}

    for r in results:
        if float(r.get("similarity") or 0) < floor:
            continue  # relevance floor — the precision knob
        sec = _section_for(r.get("category"))
        key = (r.get("source_table"), r.get("source_id"))
        if key in seen[sec] or len(out[sec]) >= cap:
            continue  # dedupe by source; cap per section
        seen[sec].add(key)
        out[sec].append(_item(r))
    return out
