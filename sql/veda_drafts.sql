-- =====================================================================
-- veda_drafts.sql — the content flywheel's OUTPUT (Phase 7).
-- Run once in the Supabase SQL Editor.
-- ---------------------------------------------------------------------
-- When AskVeda answers a gap from a trusted external source, it writes the
-- answer here as a DRAFT for a human to review, edit and publish as real
-- ParentVeda content. Then the next mother who asks gets a cheap, grounded,
-- OWNED answer — the gap closes itself.
--
-- WHY ITS OWN TABLE (not `articles` with status='draft'):
--   1. Safety — machine-written medical text must never sit in the editorial
--      table where a mis-click could publish it. Publishing stays a deliberate,
--      human act of copying an approved draft across.
--   2. Ownership — `articles` belongs to the Directus/editorial workflow. AskVeda
--      writing into it would need INSERT rights on someone else's table.
--   3. Audit — we keep the SOURCE URLs, which a medical reviewer needs and the
--      articles schema has nowhere to put.
-- Register this as a collection in Directus and it becomes an "AI drafts" inbox.
-- =====================================================================

create table if not exists public.veda_drafts (
  id            uuid        primary key default gen_random_uuid(),
  question_norm text        not null unique,   -- one draft per question
  question      text        not null,          -- the mother's actual wording
  draft_body    text        not null,          -- machine-written, UNREVIEWED
  sources       jsonb       not null default '[]'::jsonb,  -- [{title,url}] for review
  stage_key     text        not null default '',
  -- pending → approved (an editor turned it into a real article) | rejected
  status        text        not null default 'pending',
  reviewer_note text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists veda_drafts_status_idx on public.veda_drafts (status, created_at desc);

alter table public.veda_drafts enable row level security;
grant select, insert, update, delete on public.veda_drafts to service_role;

-- =====================================================================
-- VERIFY:  select question, status, jsonb_array_length(sources) as srcs
--          from public.veda_drafts order by created_at desc;
-- =====================================================================
