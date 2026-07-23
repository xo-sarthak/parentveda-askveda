-- =====================================================================
-- veda_match_function.sql — the similarity-search function (Phase 3).
-- Run once in the Supabase SQL Editor (like veda_setup.sql).
-- ---------------------------------------------------------------------
-- WHY a function? Our code talks to Supabase over PostgREST (a REST/JSON
-- layer). PostgREST can't express "ORDER BY embedding <=> $query" through a
-- plain REST call. So we wrap that vector search in a Postgres FUNCTION and
-- call it by name via supabase.rpc(...). This also keeps the vector math in
-- the database (fast, uses the HNSW index) instead of hauling every row into
-- Python.
--
-- What it does: given a question's embedding, return the `match_count` chunks
-- whose vectors are CLOSEST (smallest cosine distance = most similar meaning),
-- newest-similar first, with a `similarity` score (1.0 = identical meaning).
-- =====================================================================

create or replace function public.match_veda_content_chunks(
  query_embedding vector(384),      -- the user's question, embedded (same 384-dim model)
  match_count     int  default 3,   -- how many chunks to return (our top-k)
  filter_domain   text default null -- optional: 'pregnancy' | 'parenting' (null = all)
)
returns table (
  id           uuid,
  source_table text,
  source_id    uuid,
  chunk_index  int,
  title        text,
  slug         text,
  url          text,
  domain       text,
  week         int,
  trimester    text,
  category     text,
  verdict      text,
  chunk_text   text,
  similarity   float               -- 1 - cosine_distance; higher = closer in meaning
)
language sql
stable
as $$
  select
    c.id, c.source_table, c.source_id, c.chunk_index,
    c.title, c.slug, c.url, c.domain, c.week, c.trimester,
    c.category, c.verdict, c.chunk_text,
    1 - (c.embedding <=> query_embedding) as similarity
  from public.veda_content_chunks c
  where filter_domain is null or c.domain = filter_domain
  order by c.embedding <=> query_embedding   -- <=> = cosine distance; ASC = closest first
  limit match_count;
$$;

-- Our server calls this as service_role — let that role execute it.
grant execute on function public.match_veda_content_chunks(vector, int, text) to service_role;

-- =====================================================================
-- VERIFY (optional): this should return rows (a zero-vector just tests wiring):
--   select title, round(similarity::numeric, 3) as similarity
--   from match_veda_content_chunks(
--     (select embedding from public.veda_content_chunks limit 1), 3, null);
-- Expect: 3 rows, the first with similarity ≈ 1.0 (a chunk vs itself).
-- =====================================================================
