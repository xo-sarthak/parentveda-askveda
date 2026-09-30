"""
Import the app's exported knowledge into Supabase (`veda_knowledge`).

    python -m ingest.import_corpus [path/to/veda_corpus.json] [--prune]

The JSON is produced in the APP repo by:
    flutter test tool/export_veda_corpus.dart      →  build/veda_corpus.json

This script only READS that file — it never writes to the app repo.

Idempotent: rows are upserted on `doc_id`, so re-running after a re-export
UPDATES existing knowledge instead of duplicating it.

EXCLUSIONS: community docs are dropped. That's a standing product rule — community
posts are people's opinions, and they must never be a source for an answer.
"""

import json
import sys
from pathlib import Path

from app.db import supabase

DEFAULT_JSON = Path(r"C:\Projects\parentveda\build\veda_corpus.json")

# Never used to ground an answer (opinions, not knowledge).
EXCLUDED_KINDS = {"community"}


def _rows(path: Path) -> list[dict]:
    docs = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    skipped = 0
    for d in docs:
        if d.get("kind") in EXCLUDED_KINDS:
            skipped += 1
            continue
        title = (d.get("title") or "").strip()
        body = (d.get("body") or "").strip()
        if not title or not body:
            skipped += 1
            continue
        rows.append({
            "doc_id": d["doc_id"],
            "kind": d.get("kind") or "unknown",
            "domain": d.get("domain") or "pregnancy",
            "source_label": d.get("source_label"),
            "title": title,
            "body": body,
            "title_hi": (d.get("title_hi") or "").strip() or None,
            "body_hi": (d.get("body_hi") or "").strip() or None,
            "keywords": d.get("keywords") or [],
        })
    print(f"Prepared {len(rows)} rows ({skipped} skipped: excluded kinds / empty).")
    return rows


def _prune(rows: list[dict]) -> None:
    """Remove knowledge the app no longer has, for the domains this export holds.

    ⚠️ WHY (2026-09-30): the import only ever UPSERTED, so a read, card or
    answer that left the app stayed in the pool and could still ground an
    answer or be pointed to. With --prune, every doc of an exported domain
    whose doc_id is not in this export is deleted, with its chunks. Scoped to
    the export's own domains, so a trying-to-conceive export can never touch
    pregnancy or parenting knowledge.
    """
    keep = {r["doc_id"] for r in rows}
    for domain in sorted({r["domain"] for r in rows}):
        have: list[dict] = []
        start = 0
        while True:
            page = (supabase.table("veda_knowledge").select("id,doc_id")
                    .eq("domain", domain).range(start, start + 999).execute().data)
            have += page
            if len(page) < 1000:
                break
            start += 1000
        gone = [h for h in have if h["doc_id"] not in keep]
        for g in gone:
            supabase.table("veda_content_chunks").delete()                 .eq("source_table", "veda_knowledge").eq("source_id", g["id"]).execute()
            supabase.table("veda_knowledge").delete().eq("id", g["id"]).execute()
        print(f"  pruned {len(gone)} of {len(have)} '{domain}' docs no longer in the app")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    prune = "--prune" in sys.argv
    path = Path(args[0]) if args else DEFAULT_JSON
    if not path.exists():
        print(f"[ERROR] Not found: {path}")
        print("Run this in the APP repo first:")
        print("   flutter test tool/export_veda_corpus.dart")
        return

    rows = _rows(path)
    if not rows:
        print("Nothing to import.")
        return

    batch = 200
    for start in range(0, len(rows), batch):
        supabase.table("veda_knowledge").upsert(
            rows[start:start + batch], on_conflict="doc_id"
        ).execute()
        print(f"  upserted {min(start + batch, len(rows))}/{len(rows)}")

    if prune:
        _prune(rows)

    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    print(f"\n[OK] {len(rows)} docs in veda_knowledge.")
    for k in sorted(by_kind, key=lambda x: -by_kind[x]):
        print(f"   {k:<16} {by_kind[k]}")
    print("\nNext: python -m ingest.ingest   (re-chunk + re-embed everything)")


if __name__ == "__main__":
    main()
