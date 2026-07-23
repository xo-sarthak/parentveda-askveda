-- =====================================================================
-- veda_setup.sql — AskVeda's database brain (run once in the Supabase SQL Editor).
-- ---------------------------------------------------------------------
-- Creates everything AskVeda needs INSIDE the shared ParentVeda Supabase:
--   1. the `vector` extension (pgvector) — lets Postgres store + search vectors
--   2. veda_content_chunks — the searchable content index (our knowledge base)
--   3. veda_cache          — remembered Q&A, to skip the LLM on repeats
--   4. veda_usage_log      — every call, for rate limits + spend cap + metrics
--
-- All three are SERVER-SIDE ONLY: the AskVeda service reaches them with the
-- Supabase service_role key. The app/website never touch them. Uniquely named
-- (veda_*) so they don't clash with the app's tables.
--
-- Safe to run once; `if not exists` makes a re-run harmless.
-- =====================================================================


-- 1) Enable pgvector --------------------------------------------------
-- Postgres can't store "a vector" out of the box. This extension adds a `vector`
-- column type, similarity operators (`<=>` = cosine distance), and vector
-- indexes. One line, enabled for the whole database.
create extension if not exists vector;


-- 2) veda_content_chunks — the knowledge base (replaces Chroma) -------
-- Each row = ONE chunk of one article/post + its embedding (the chunk's meaning
-- as 384 numbers). At query time we embed the user's question and find the chunks
-- whose vectors are CLOSEST — that's "retrieval".
create table if not exists public.veda_content_chunks (
  id           uuid        primary key default gen_random_uuid(),
  -- WHERE this chunk came from (for attribution + clean re-indexing):
  source_table text        not null,           -- 'articles' | 'content_posts'
  source_id    uuid        not null,            -- the article/post id
  chunk_index  int         not null,            -- 0,1,2… position within that source
  -- METADATA carried for filtering + safety + attribution:
  title        text,
  slug         text,                            -- content_posts have slugs; articles don't
  url          text,                            -- for "source" links later
  domain       text,                            -- pregnancy | parenting | universal
  week         int,                             -- articles.week (nullable)
  trimester    text,                            -- content_posts.trimester (nullable)
  category     text,
  verdict      text,                            -- yes|moderation|avoid-some|avoid (Can-I)
  -- THE CONTENT + ITS EMBEDDING:
  chunk_text   text        not null,            -- the actual words the LLM will read
  embedding    vector(384) not null,            -- 384 dims = bge-small-en-v1.5 / all-MiniLM
  updated_at   timestamptz not null default now(),
  -- one row per (source, chunk) → re-ingesting UPDATES instead of duplicating:
  unique (source_table, source_id, chunk_index)
);

-- Similarity-search index. Without it, finding the closest vectors scans EVERY
-- row (fine for hundreds, slow at scale). HNSW builds a graph that finds nearest
-- neighbours fast. `vector_cosine_ops` = compare by COSINE similarity, the right
-- measure for these normalized text embeddings.
create index if not exists veda_content_chunks_embedding_idx
  on public.veda_content_chunks
  using hnsw (embedding vector_cosine_ops);

-- Lock it down: RLS on; only the service_role (our server) may touch it.
alter table public.veda_content_chunks enable row level security;
grant select, insert, update, delete on public.veda_content_chunks to service_role;


-- 3) veda_cache — remembered answers (skip the LLM on repeats) --------
-- Two ways to hit: EXACT (normalized text identical) or SEMANTIC (a new question's
-- embedding is very close to a cached one). A hit returns the stored answer for
-- ~₹0 — no retrieval, no LLM.
create table if not exists public.veda_cache (
  id                 uuid        primary key default gen_random_uuid(),
  question_norm      text        not null,      -- lowercased/stripped → exact-match key
  cached_question    text        not null,      -- the original wording (reference)
  question_embedding vector(384) not null,      -- for semantic matching
  -- week/trimester folded in so "X at week 8" and "X at week 30" NEVER collide in
  -- the cache (they embed almost identically but need different answers):
  week_key           text        not null default '',
  answer             text        not null,
  hit_count          int         not null default 0,    -- reuse count → surfaces FAQs
  last_used          timestamptz not null default now(),-- for expiry when content changes
  created_at         timestamptz not null default now(),
  unique (question_norm, week_key)              -- exact-match dedup key
);

create index if not exists veda_cache_embedding_idx
  on public.veda_cache
  using hnsw (question_embedding vector_cosine_ops);

alter table public.veda_cache enable row level security;
grant select, insert, update, delete on public.veda_cache to service_role;


-- 4) veda_usage_log — one row per answered question -------------------
-- Powers three things: per-user RATE LIMITS (count today's rows for a user), the
-- GLOBAL DAILY SPEND CAP (sum today's cost), and METRICS (cache-hit rate,
-- $/answer). This keeps the bill honest and measurable from day one.
create table if not exists public.veda_usage_log (
  id            uuid          primary key default gen_random_uuid(),
  channel       text          not null,        -- 'app' | 'whatsapp'
  user_key      text          not null,        -- app: supabase user id; wa: phone
  cache_hit     boolean       not null default false,
  used_web      boolean       not null default false,  -- Case C (hybrid web fallback)
  input_tokens  int           not null default 0,
  output_tokens int           not null default 0,
  cost_usd      numeric(10,6) not null default 0,
  created_at    timestamptz   not null default now()
);

-- Fast "how many has THIS user done today?" (rate limit) and
-- "what's today's total spend?" (spend cap):
create index if not exists veda_usage_user_time_idx
  on public.veda_usage_log (user_key, created_at desc);
create index if not exists veda_usage_time_idx
  on public.veda_usage_log (created_at desc);

alter table public.veda_usage_log enable row level security;
grant select, insert, update, delete on public.veda_usage_log to service_role;


-- 5) Let AskVeda's server role READ the content it ingests -----------
-- The content tables (articles, content_posts) were created granting SELECT to
-- anon/authenticated (the app + website read them publicly). AskVeda's server
-- connects as service_role, which BYPASSES RLS but still needs an explicit table
-- grant. Additive + safe — the app/website's public-read access is unchanged.
grant select on public.articles      to service_role;
grant select on public.content_posts to service_role;


-- =====================================================================
-- VERIFY (run these after, to confirm it worked):
--   select extname from pg_extension where extname = 'vector';
--   select table_name from information_schema.tables
--     where table_schema = 'public' and table_name like 'veda_%';
-- Expect: vector · veda_content_chunks, veda_cache, veda_usage_log.
-- =====================================================================
