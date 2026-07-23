-- =====================================================================
-- veda_cache_function.sql — semantic cache search (Phase 4).
-- Run once in the Supabase SQL Editor (like the earlier two SQL files).
-- ---------------------------------------------------------------------
-- The cache has TWO ways to hit:
--   • EXACT   — same normalized text + same week_key (a plain table lookup, no
--               function needed).
--   • SEMANTIC — a NEW question whose meaning is almost identical to a cached one
--               (embedding within the similarity threshold). THAT needs a vector
--               search, so — exactly like content retrieval — we wrap it in a
--               Postgres function and call it over RPC.
--
-- Safety: we ALSO require the same `week_key`, so "safe at week 8" and "safe at
-- week 30" can never be served each other's answer even if the wording matches.
-- =====================================================================

create or replace function public.match_veda_cache(
  query_embedding vector(384),        -- the new question, embedded
  week_key_in     text  default '',   -- must match (week/trimester bucket)
  match_count     int   default 1,
  min_similarity  float default 0.95  -- only reuse if THIS close in meaning
)
returns table (
  id              uuid,
  cached_question text,
  answer          text,
  week_key        text,
  similarity      float
)
language sql
stable
as $$
  select
    c.id, c.cached_question, c.answer, c.week_key,
    1 - (c.question_embedding <=> query_embedding) as similarity
  from public.veda_cache c
  where c.week_key = week_key_in
    and 1 - (c.question_embedding <=> query_embedding) >= min_similarity
  order by c.question_embedding <=> query_embedding   -- closest first
  limit match_count;
$$;

grant execute on function public.match_veda_cache(vector, text, int, float) to service_role;

-- =====================================================================
-- VERIFY (optional): with an empty cache this returns 0 rows (that's fine —
-- it just confirms the function exists and runs):
--   select * from match_veda_cache(
--     (select embedding from public.veda_content_chunks limit 1), '', 1, 0.95);
-- =====================================================================
