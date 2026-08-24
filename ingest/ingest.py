"""
Ingestion — fill `veda_content_chunks` from the PUBLISHED content in Supabase.

Two entry points:
  • ingest()                     — full re-embed of everything (CLI / cron / big change)
  • reindex_source(table, id)    — re-embed ONE row (Directus publish webhook, Phase 8)

Both share the same chunk→embed→upsert path. Upsert is keyed on
(source_table, source_id, chunk_index), so re-running UPDATES rows. For a single
source we DELETE its old chunks first, which also handles a body that shrank to
fewer chunks, or a row that got unpublished/deleted.

    python -m ingest.ingest
"""

from app.db import supabase
from app.embeddings import embed_documents
from ingest.chunker import chunk_text

# ---- per-source spec: (columns to fetch, how to map a row → chunk metadata) ----
# Adding a new content table is a one-entry change here.


def _base_article(a: dict) -> dict:
    return {"title": a.get("title"), "domain": a.get("domain"),
            "week": a.get("week"), "category": a.get("category")}


def _base_post(p: dict) -> dict:
    return {"title": p.get("title"), "slug": p.get("slug"), "week": p.get("week_tag"),
            "trimester": p.get("trimester"), "category": p.get("category"),
            "verdict": p.get("verdict")}


def _base_knowledge(k: dict) -> dict:
    return {"title": k.get("title"), "domain": k.get("domain"), "category": k.get("kind")}


def _base_recipe(r: dict) -> dict:
    return {"title": r.get("title"), "domain": r.get("domain"),
            "category": r.get("category")}


def _base_read(r: dict) -> dict:
    return {"title": r.get("title"), "domain": r.get("domain"),
            "category": r.get("collection")}


def _base_product(p: dict) -> dict:
    return {"title": p.get("name"), "domain": p.get("domain"),
            "category": p.get("category")}


# ---- how a row becomes text --------------------------------------------------
#
# The first three sources each keep their prose in one `body` column, so a spec
# only ever had to say WHICH columns to fetch. The app's content tables
# (migrations 0047-0049) do not: a recipe's teaching is spread across `why`,
# `healthier_note`, `ingredients`, `steps` and `mistakes`, and a read's whole
# article lives inside a `sections` jsonb blob.
#
# So a spec now carries a THIRD element: how to flatten a row into the text we
# embed. The alternative was adding a `body` column to each table kept in step
# by a trigger -- a second copy of prose that already exists, and drift between
# the copy and the original would be invisible until an answer quoted a
# paragraph the article no longer contains. Better to teach the reader to read.


def _lines(*parts) -> str:
    """Join the non-empty pieces with newlines. Anything falsy disappears."""
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        s = str(p).strip()
        if s:
            out.append(s)
    return "\n".join(out)


def _bullets(label: str, items) -> str:
    """A jsonb array as a labelled list. Handles plain strings and the
    {name, amount, note} shape `nutrients` uses."""
    if not items:
        return ""
    rows: list[str] = []
    for it in items:
        if isinstance(it, dict):
            joined = " ".join(
                str(it.get(k)) for k in ("name", "amount", "note") if it.get(k))
            if joined.strip():
                rows.append(joined.strip())
        elif it and str(it).strip():
            rows.append(str(it).strip())
    if not rows:
        return ""
    return f"{label}:\n" + "\n".join(f"- {r}" for r in rows)


def _text_body(row: dict) -> str:
    return row.get("body") or ""


def _text_recipe(r: dict) -> str:
    return _lines(
        r.get("title"), r.get("subtitle"),
        f"For {r.get('age_tag')}" if r.get("age_tag") else "",
        r.get("highlight"), r.get("why"),
        _bullets("Ingredients", r.get("ingredients")),
        _bullets("Steps", r.get("steps")),
        _bullets("Nutrition", r.get("nutrients")),
        _bullets("Storage", r.get("storage")),
        # The mistakes are the most useful part of a recipe and the thing a
        # parent actually asks about -- "why did my ragi porridge go lumpy".
        _bullets("Common mistakes", r.get("mistakes")),
        r.get("healthier_note"),
    )


def _text_read(r: dict) -> str:
    """A read's body is a list of blocks, not prose. Every block type has to be
    flattened -- a myth-vs-fact card missed here is a myth Ask Veda cannot
    correct, and it would be missed silently."""
    parts = [r.get("title"), r.get("teaser"), r.get("why_today"), r.get("evidence")]
    for block in (r.get("sections") or []):
        if not isinstance(block, dict):
            continue
        parts.append(block.get("heading"))
        parts.extend(block.get("paragraphs") or [])
        tip = block.get("tip")
        if isinstance(tip, dict):
            parts.append(_lines(tip.get("title"), tip.get("body")))
        mf = block.get("mythFact")
        if isinstance(mf, dict) and (mf.get("myth") or mf.get("fact")):
            parts.append(f"Myth: {mf.get('myth')}\nFact: {mf.get('fact')}")
    return _lines(*parts)


def _text_product(p: dict) -> str:
    """⚠️ PROS **AND** CONS, always.

    Grounding an answer on the good half only is an advert wearing a
    recommendation's clothes. That was a real defect in the app's parenting
    corpus, fixed alongside migration 0049, and it is why both halves live in
    one row -- so the rule can be checked rather than remembered. If this ever
    needs trimming for token budget, trim `specs`, never `cons`."""
    return _lines(
        _lines(p.get("brand"), p.get("name")),
        f"Around Rs {p.get('price_inr')}" if p.get("price_inr") else "",
        p.get("best_for"), p.get("summary"),
        _bullets("What is good", p.get("pros")),
        _bullets("What to watch out for", p.get("cons")),
        _bullets("Specs",
                 [f"{k}: {v}" for k, v in (p.get("specs") or {}).items()]),
    )


# table → (select columns, base-metadata builder, row→text builder)
#
# Adding a content table is still a one-entry change; the entry now says how to
# READ the table as well as which columns to pull.
SOURCE_SPECS: dict[str, tuple[str, object, object]] = {
    "articles":
        ("id, domain, week, category, title, body", _base_article, _text_body),
    "content_posts":
        ("id, category, slug, title, body, trimester, week_tag, verdict",
         _base_post, _text_body),
    "veda_knowledge":
        ("id, doc_id, kind, domain, title, body", _base_knowledge, _text_body),

    # ---- the app's content tables (migrations 0047-0049) ----------------
    # Authored in Directus, read by the app straight from Supabase, and until
    # now invisible here: a recipe could be live for weeks and still be
    # something Ask Veda had never heard of. Not broken -- UNAWARE, which is
    # worse, because nothing errors and the only symptom is an answer that
    # says "I don't know" about content we published ourselves.
    "recipes": (
        "id, domain, category, title, subtitle, age_tag, highlight, why, "
        "healthier_note, ingredients, steps, storage, mistakes, nutrients",
        _base_recipe, _text_recipe),
    "reads": (
        "id, domain, collection, title, teaser, why_today, evidence, sections",
        _base_read, _text_read),
    "products": (
        "id, domain, category, name, brand, price_inr, best_for, summary, "
        "pros, cons, specs",
        _base_product, _text_product),
}


def _to_vector_literal(vec: list[float]) -> str:
    """pgvector's text input format: '[0.1,0.2,...]' — the reliable way through
    Supabase/PostgREST into a vector() column."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _fetch_published(table: str, columns: str) -> list[dict]:
    """Read all published rows of a content table (paged)."""
    rows: list[dict] = []
    page, size = 0, 1000
    while True:
        res = (
            supabase.table(table)
            .select(columns)
            .eq("status", "published")
            .range(page * size, page * size + size - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < size:
            break
        page += 1
    return rows


def _chunk_rows_for(table: str, row: dict) -> list[dict]:
    """One content row → its chunk rows (each = a piece of body + shared metadata)."""
    _, base_fn, text_fn = SOURCE_SPECS[table]
    base = base_fn(row)
    out: list[dict] = []
    # text_fn, not row["body"]: three of the six sources have no body column,
    # and reading one would have produced zero chunks in silence.
    for i, text in enumerate(chunk_text(text_fn(row))):
        out.append({
            "source_table": table,
            "source_id": row["id"],
            "chunk_index": i,
            **base,
            "chunk_text": text,
        })
    return out


def _embed_and_upsert(chunk_rows: list[dict]) -> int:
    """Embed every chunk (one batch) and upsert into veda_content_chunks."""
    if not chunk_rows:
        return 0
    vectors = embed_documents([r["chunk_text"] for r in chunk_rows])
    for row, vec in zip(chunk_rows, vectors):
        row["embedding"] = _to_vector_literal(vec)
    for start in range(0, len(chunk_rows), 200):
        supabase.table("veda_content_chunks").upsert(
            chunk_rows[start:start + 200],
            on_conflict="source_table,source_id,chunk_index",
        ).execute()
    return len(chunk_rows)


def _delete_source_chunks(table: str, source_id: str) -> None:
    supabase.table("veda_content_chunks").delete() \
        .eq("source_table", table).eq("source_id", source_id).execute()


def reindex_source(table: str, source_id: str) -> dict:
    """Re-embed ONE content row (Phase 8 — the Directus publish webhook calls this).

    Deletes the row's existing chunks first, so an edit that shrank the body, or an
    unpublish/delete, is reflected correctly (stale chunks don't linger).
    """
    if table not in SOURCE_SPECS:
        return {"ok": False, "error": f"unknown source_table '{table}'"}

    cols, _, _ = SOURCE_SPECS[table]
    res = (
        supabase.table(table).select(f"{cols}, status")
        .eq("id", source_id).limit(1).execute()
    )
    rows = res.data or []

    # Always clear old chunks first.
    _delete_source_chunks(table, source_id)

    # Gone or unpublished → we've already removed it from the index. Done.
    if not rows or rows[0].get("status") != "published":
        return {"ok": True, "source_id": source_id, "chunks": 0,
                "note": "removed (missing or unpublished)"}

    n = _embed_and_upsert(_chunk_rows_for(table, rows[0]))
    return {"ok": True, "source_id": source_id, "chunks": n}


def ingest() -> None:
    """Full re-embed of every published source. Idempotent."""
    chunk_rows: list[dict] = []
    counts: dict[str, int] = {}
    for table, (cols, _, _tf) in SOURCE_SPECS.items():
        rows = _fetch_published(table, cols)
        counts[table] = len(rows)
        for row in rows:
            chunk_rows.extend(_chunk_rows_for(table, row))

    if not chunk_rows:
        print("No published content found — nothing to ingest.")
        return

    summary = " + ".join(f"{n} {t}" for t, n in counts.items())
    print(f"Chunked {summary} -> {len(chunk_rows)} chunks.")
    print("Embedding (first run downloads the model, ~a minute)...")
    n = _embed_and_upsert(chunk_rows)
    print(f"\n[OK] Ingested {n} chunks into veda_content_chunks.")


if __name__ == "__main__":
    ingest()
