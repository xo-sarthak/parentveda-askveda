-- veda_chunks_hash.sql -- the fingerprint that makes the full ingest incremental
-- (2026-10-17). Run once in the Supabase SQL editor, BEFORE the next
-- `python -m ingest.ingest`. Additive and re-runnable.
--
-- content_hash = sha256(embed model + "\n" + chunk text), written by
-- ingest/ingest.py. A run embeds only chunks whose hash is new or different,
-- and deletes chunks no source produces any more. Existing rows start NULL, so
-- the first run after this re-embeds everything once (the last full, hot run).

alter table public.veda_content_chunks
  add column if not exists content_hash text;
