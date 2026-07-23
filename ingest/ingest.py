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


# table → (select columns, base-metadata builder)
SOURCE_SPECS: dict[str, tuple[str, object]] = {
    "articles": ("id, domain, week, category, title, body", _base_article),
    "content_posts":
        ("id, category, slug, title, body, trimester, week_tag, verdict", _base_post),
    "veda_knowledge": ("id, doc_id, kind, domain, title, body", _base_knowledge),
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
    _, base_fn = SOURCE_SPECS[table]
    base = base_fn(row)
    out: list[dict] = []
    for i, text in enumerate(chunk_text(row.get("body"))):
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

    cols, _ = SOURCE_SPECS[table]
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
    for table, (cols, _) in SOURCE_SPECS.items():
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
