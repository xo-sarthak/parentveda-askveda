-- =====================================================================
-- veda_knowledge.sql — the app's own knowledge, in the shared database.
-- Run once in the Supabase SQL Editor.
-- ---------------------------------------------------------------------
-- WHY a separate table (not `articles` / `content_posts`): those belong to the
-- editorial/Directus workflow. This table holds knowledge EXPORTED from the
-- Flutter app's offline corpus (Can-I verdicts, symptoms, scan guides, Garbh
-- readings, recipes, parenting knowledge, the seeded Ask Veda answers…). Keeping
-- it distinct means a re-export can never clobber editor-authored content.
--
-- The RAG ingest reads this table alongside articles + content_posts, so BOTH
-- sides of the app (pregnancy and parenting) ground on ONE database — the same
-- mother, one continuous journey, no per-side content silos.
--
-- `kind` is kept on every row (and copied onto each chunk) so a category can be
-- excluded from retrieval later with a one-line filter — no re-export needed.
-- =====================================================================

create table if not exists public.veda_knowledge (
  id           uuid        primary key default gen_random_uuid(),
  doc_id       text        not null unique,  -- the app's VedaDoc id → re-import is idempotent
  kind         text        not null,         -- canI | symptom | scan | spiritual | recipe | …
  domain       text        not null default 'pregnancy',  -- pregnancy | parenting | universal
  source_label text,
  title        text        not null,
  body         text        not null,         -- English (what we embed today)
  -- Hinglish is stored now so answers can be served bilingually later. We do NOT
  -- embed it yet: our embedding model is English (bge-small-en), so mixing
  -- languages in one index would hurt retrieval.
  title_hi     text,
  body_hi      text,
  keywords     text[],
  status       text        not null default 'published',
  updated_at   timestamptz not null default now()
);

create index if not exists veda_knowledge_kind_idx   on public.veda_knowledge (kind);
create index if not exists veda_knowledge_domain_idx on public.veda_knowledge (domain);

alter table public.veda_knowledge enable row level security;
grant select, insert, update, delete on public.veda_knowledge to service_role;

-- =====================================================================
-- VERIFY (after running the importer):
--   select kind, count(*) from public.veda_knowledge group by kind order by 2 desc;
-- =====================================================================
