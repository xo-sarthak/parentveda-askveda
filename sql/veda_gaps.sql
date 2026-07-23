-- =====================================================================
-- veda_gaps.sql — the CONTENT GAP table (the flywheel's input).
-- Run once in the Supabase SQL Editor.
-- ---------------------------------------------------------------------
-- Every time AskVeda CANNOT answer from our content, we record the question
-- here. Repeats increment `ask_count` instead of creating duplicates — so this
-- table becomes a demand-ranked list of "content we should write next".
--
--   select question, ask_count, stage_key, last_asked
--   from veda_content_gaps where status = 'open'
--   order by ask_count desc;
--
-- That query IS your content to-do list. A high ask_count = many mothers asked
-- and got nothing. Writing that one article converts every future ask into a
-- cheap, grounded answer.
-- =====================================================================

create table if not exists public.veda_content_gaps (
  id            uuid        primary key default gen_random_uuid(),
  question_norm text        not null unique,   -- dedupe key (normalized text)
  question      text        not null,          -- a readable example of the wording
  ask_count     int         not null default 1,-- THE DEMAND SIGNAL
  stage_key     text        not null default '',-- where the asker was (helps writing)
  first_asked   timestamptz not null default now(),
  last_asked    timestamptz not null default now(),
  -- open → drafted (auto-drafted into Directus) → published (content now exists)
  status        text        not null default 'open',
  notes         text
);

-- "What should we write next?" and "what's still open?"
create index if not exists veda_gaps_count_idx  on public.veda_content_gaps (ask_count desc);
create index if not exists veda_gaps_status_idx on public.veda_content_gaps (status, ask_count desc);

alter table public.veda_content_gaps enable row level security;
grant select, insert, update, delete on public.veda_content_gaps to service_role;


-- Atomic "record this gap" — insert, or bump the counter if we've seen it.
-- Done as a function so two simultaneous askers can't clobber each other's count.
create or replace function public.log_veda_gap(
  q_norm text,
  q      text,
  stage  text default ''
)
returns void
language plpgsql
as $$
begin
  insert into public.veda_content_gaps (question_norm, question, stage_key)
  values (q_norm, q, stage)
  on conflict (question_norm) do update
    set ask_count  = public.veda_content_gaps.ask_count + 1,
        last_asked = now(),
        stage_key  = excluded.stage_key;
end;
$$;

grant execute on function public.log_veda_gap(text, text, text) to service_role;

-- =====================================================================
-- VERIFY:  select * from public.veda_content_gaps;      -- empty at first
-- =====================================================================
