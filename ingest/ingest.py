"""
Ingestion — fill `veda_content_chunks` from the PUBLISHED content in Supabase.

Two entry points:
  • ingest()                     — full re-embed of everything (CLI / cron / big change)
  • reindex_source(table, id)    — re-embed ONE row (Directus publish webhook, Phase 8)

Both share the same chunk→embed→upsert path. Upsert is keyed on
(source_table, source_id, chunk_index), so re-running UPDATES rows. For a single
source we DELETE its old chunks first, which also handles a body that shrank to
fewer chunks, or a row that got unpublished/deleted.

The full run is INCREMENTAL (2026-10-17). Chunking is cheap and embedding is
not (8,856 chunks took minutes at 95 °C on a laptop CPU), so every chunk
carries a fingerprint, `content_hash`, and a run embeds only chunks that are new
or whose fingerprint changed. It also DELETES chunks no source produces any
more: before this, a full run only upserted, so an unpublished recipe or the
tail of a shortened article stayed answerable for ever.

    python -m ingest.ingest              # only what changed
    python -m ingest.ingest --only recipes
    python -m ingest.ingest --full       # re-embed everything

Needs `content_hash` on veda_content_chunks: sql/veda_chunks_hash.sql.
"""

import argparse
import hashlib

from app.config import settings
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


def content_hash(text: str) -> str:
    """The chunk's fingerprint: the exact embedded text AND the model name.

    The model is in it because a vector is a function of both. Switch
    `embed_model` and every fingerprint changes, so the next run re-embeds
    everything without anyone having to remember `--full`."""
    return hashlib.sha256(f"{settings.embed_model}\n{text}".encode("utf-8")).hexdigest()


Key = tuple[str, str, int]  # (source_table, source_id, chunk_index)


def plan_changes(
    produced: dict[Key, str],
    existing: dict[Key, "str | None"],
    readable: set[str],
    *,
    full: bool = False,
) -> tuple[list[Key], list[Key]]:
    """Decide what a run does. Pure, so it is tested without Supabase.

    produced: every chunk the sources yield now -> its hash.
    existing: every chunk already in the index -> its stored hash (None for a
              row written before hashes existed, which therefore re-embeds once).
    readable: the tables actually read this run.

    Returns (to_embed, stale).
      to_embed: new keys, and keys whose hash differs (all of them when full).
      stale:    keys in the index that no source produced, ONLY for tables read
                this run. A table that failed to read produced nothing, and
                treating that as "everything was removed" would wipe it from
                Ask Veda over one permission error.
    """
    to_embed = [k for k, h in produced.items() if full or existing.get(k) != h]
    stale = [k for k in existing if k not in produced and k[0] in readable]
    return to_embed, stale


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
            "content_hash": content_hash(text),
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


def _fetch_index(tables: set[str]) -> dict:
    """Every chunk already indexed for these tables: key -> (row id, hash). Paged."""
    out: dict = {}
    for table in sorted(tables):
        page, size = 0, 1000
        while True:
            res = (
                supabase.table("veda_content_chunks")
                .select("id, source_table, source_id, chunk_index, content_hash")
                .eq("source_table", table)
                .range(page * size, page * size + size - 1)
                .execute()
            )
            batch = res.data or []
            for r in batch:
                key = (r["source_table"], str(r["source_id"]), int(r["chunk_index"]))
                out[key] = (r["id"], r.get("content_hash"))
            if len(batch) < size:
                break
            page += 1
    return out


def ingest(only: "str | None" = None, full: bool = False) -> None:
    """Embed what changed, remove what is gone. A re-run with no content change
    embeds nothing and removes nothing."""
    if only and only not in SOURCE_SPECS:
        print(f"Unknown --only '{only}'. One of: {', '.join(SOURCE_SPECS)}")
        return
    tables = [only] if only else list(SOURCE_SPECS)

    chunk_rows: list[dict] = []
    counts: dict[str, int] = {}
    readable: set[str] = set()
    for table in tables:
        cols = SOURCE_SPECS[table][0]
        # ⚠️ ONE UNREADABLE TABLE MUST NOT STOP THE REST (2026-09-30). The
        # service role once lacked SELECT on recipes, reads and products, and a
        # permission error here used to end the whole run. Now the table is
        # skipped, loudly, and (2026-10-17) left out of `readable`, so none of
        # its existing chunks are treated as removed.
        try:
            rows = _fetch_published(table, cols)
        except Exception as e:  # noqa: BLE001 — any read failure skips the table
            print(f"[SKIP] {table}: {str(e)[:160]}")
            counts[table] = 0
            continue
        readable.add(table)
        counts[table] = len(rows)
        for row in rows:
            chunk_rows.extend(_chunk_rows_for(table, row))

    summary = " + ".join(f"{n} {t}" for t, n in counts.items())
    print(f"Chunked {summary} -> {len(chunk_rows)} chunks.")

    by_key = {
        (r["source_table"], str(r["source_id"]), r["chunk_index"]): r
        for r in chunk_rows
    }
    index = _fetch_index(readable)
    to_embed, stale = plan_changes(
        {k: r["content_hash"] for k, r in by_key.items()},
        {k: h for k, (_id, h) in index.items()},
        readable,
        full=full,
    )

    if to_embed:
        print(f"Embedding {len(to_embed)} chunks (first run downloads the model, ~a minute)...")
        _embed_and_upsert([by_key[k] for k in to_embed])
    stale_ids = [index[k][0] for k in stale]
    for start in range(0, len(stale_ids), 200):
        supabase.table("veda_content_chunks").delete() \
            .in_("id", stale_ids[start:start + 200]).execute()

    unchanged = len(by_key) - len(to_embed)
    print(f"\n[OK] {unchanged} unchanged · {len(to_embed)} embedded · "
          f"{len(stale_ids)} removed (veda_content_chunks).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fill veda_content_chunks from published content.")
    ap.add_argument("--only", help="one source table, e.g. recipes")
    ap.add_argument("--full", action="store_true", help="re-embed every chunk")
    a = ap.parse_args()
    ingest(only=a.only, full=a.full)
