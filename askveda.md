# AskVeda — Build Log & Learning Notes

> A build-along learning journal, written to *actually understand* how each piece
> works — in plain terms, phase by phase. (A brief architecture summary lives in
> the app repo's `docs/CONTENT-BACKEND.md`; the in-depth learning is **here**.)

---

## The big picture — one brain, two doors

AskVeda answers pregnancy/parenting questions **grounded in ParentVeda's own
content**. The logic lives in **one always-on service**; the app and WhatsApp are
two **doors** into it. Neither client holds any AI logic — they just send a
question and show the answer. This service does everything:

```
Flutter app ── HTTPS ──┐
                       ├──► AskVeda service ──► Supabase (content + vectors + cache)
WhatsApp ── MSG91 ─────┘        (RAG brain)         └──► Groq (the LLM)
```

## Planned layout (grows phase by phase)

```
parentveda-askveda/
  app/
    __init__.py     makes app/ an importable package
    main.py         FastAPI entry point (routes live here)   [Phase 0]
    config.py       one typed place for all settings/secrets [Phase 0]
    db.py           Supabase client                          [Phase 2]
    embeddings.py   turn text → vectors                      [Phase 2]
    retriever.py    question → closest content chunks        [Phase 3]
    prompt.py       build the grounded LLM prompt            [Phase 3]
    llm.py          call Groq (OpenAI-compatible)            [Phase 3]
    cache.py        exact + semantic cache                   [Phase 4]
    guardrails.py   scope · red-flag · rate limit · spend    [Phase 4]
    gateway.py      normalize app|whatsapp → one format      [Phase 5]
    channels/
      app_api.py    POST /ask (verifies app login)           [Phase 5]
      whatsapp.py   MSG91 inbound webhook + reply            [Phase 6]
  ingest/
    chunker.py      split articles/posts into chunks         [Phase 2]
    ingest.py       fetch → chunk → embed → store            [Phase 2]
  sql/
    veda_setup.sql  you paste this into Supabase             [Phase 1]
  requirements.txt  .env.example  .gitignore  README.md
```

---

## Phase 0 — Skeleton & config

### What we built
`app/__init__.py`, `app/config.py`, `app/main.py` (a `/health` endpoint),
`requirements.txt`, `.env.example`, `.gitignore`, `README.md`. Then a virtualenv,
installed the deps, and confirmed the service answers a request.

### Concepts — how this actually works

**1. FastAPI is a *web framework*.** A web framework's job is to turn ordinary
Python functions into **HTTP endpoints** — things the outside world can reach over
the internet. You write a normal function, put `@app.get("/health")` on top, and
FastAPI wires it so that a browser/app hitting `GET /health` runs that function
and sends back its return value as JSON. You don't hand-parse HTTP; the framework
does it.

**2. Uvicorn is the *server* that runs the framework.** This is the split people
find confusing: **FastAPI = your app's logic; Uvicorn = the engine that actually
listens on a network port and speaks HTTP.** FastAPI by itself is just Python
objects; it can't receive network traffic. Uvicorn opens port 8000, accepts
requests, and calls into your FastAPI `app`. That's why you run
`uvicorn app.main:app` — "uvicorn, load the `app` object from `app/main.py` and
serve it." (Analogy: FastAPI is the chef and the recipes; Uvicorn is the
restaurant's front-of-house that takes orders off the street and carries them to
the kitchen.)

**3. An "endpoint" / "route" = a URL + method → a function.** `GET /health`,
`POST /ask`, `POST /whatsapp/webhook` are all routes. The **method** (GET, POST…)
is the verb: GET = "fetch/read," POST = "send data / do something." Our `/ask`
will be a POST because the app *sends* a question.

**4. ASGI (one line).** Uvicorn talks to FastAPI through a standard called **ASGI**
(Asynchronous Server Gateway Interface). It's just the agreed contract between
"the server" and "the app" so any ASGI server can run any ASGI app. You rarely
touch it directly — but it's why the `app` object is called "an ASGI app."

**5. The health endpoint & why it exists.** `/health` returns a tiny `{"status":
"ok"}`. Real hosts (Render) periodically ping an endpoint like this to know the
service is alive; if it stops answering, they restart it. For us right now, it's
the simplest possible proof that the whole chain (server → framework → our code →
JSON back) works.

**6. `config.py` + pydantic-settings — typed config, zero hardcoded secrets.**
Instead of scattering `os.environ["KEY"]` around (easy to typo, easy to leak),
we define one `Settings` class. pydantic-settings reads values from the
environment (or a local `.env`), **checks their types**, and gives one shared
`settings` object every file imports. If a required secret is missing, it fails
**at startup** with a clear error — far better than crashing mid-request in
production.

**7. `.env` vs `.env.example` vs `.gitignore` — secrets hygiene.** Secrets (API
keys, the Supabase service-role key) must **never** be committed to git — a public
repo would leak them. So: the real values live in a local **`.env`** file that
`.gitignore` blocks from ever being committed; the committed **`.env.example`** is
a *template* showing the shape (variable names, no values) so anyone can recreate
their own `.env`. This is the universal pattern for handling secrets.

**8. `requirements.txt` + virtualenv — isolated dependencies.** `requirements.txt`
lists the exact packages the project needs. A **virtualenv** (`.venv/`) is a
private, project-local copy of Python + those packages, so this project's
dependencies never clash with another project's on the same machine. You "activate"
it, `pip install -r requirements.txt` into it, and run everything from it. (It's
gitignored — anyone re-creates it from `requirements.txt`.)

**9. The `app/` package & import paths.** Because `app/` has an `__init__.py`, it's
a *package*, so `from app.config import settings` works from anywhere in the
project. Keeping code inside `app/` (not loose files) is what makes those clean,
predictable imports possible as the project grows.

### How to run it
```bash
uvicorn app.main:app --reload      # --reload = auto-restart when you edit code
# → open http://localhost:8000/health   and   http://localhost:8000/docs
```
`/docs` is a freebie: FastAPI auto-generates interactive API documentation from
your routes. You'll watch it fill up as we add endpoints.

### Verified
Ran the `/health` route in-process and it returned `200 {"status":"ok",...}` — the
skeleton is alive.

### What Phase 1 adds
The **database brain**: we enable **pgvector** in Supabase and create the tables
that will hold the content vectors, the answer cache, and the usage log. That's
where "similarity search" and "what a vector actually is" get real.

---

## Phase 1 — The database brain (pgvector)

### What we built
`sql/veda_setup.sql` — enables the `vector` extension and creates three
server-side-only tables: **veda_content_chunks** (the knowledge base),
**veda_cache** (remembered answers), **veda_usage_log** (metrics + limits). You
paste it into the Supabase SQL Editor once.

### Concepts — how this actually works

**1. What a "vector" / "embedding" actually is.** An **embedding** is a list of
numbers (ours = **384** of them) that captures the *meaning* of a piece of text.
An embedding *model* reads text and outputs this list. The magic property:
**texts with similar meaning get similar number-lists**, even if the words differ.
So *"is papaya safe in pregnancy?"* and *"can I eat papaya while pregnant"* land at
nearly the same point in "meaning space," while *"how to file taxes"* lands far
away. That's what lets us match a question to content by **meaning, not keywords**.
Think of each embedding as a coordinate; similar meanings = nearby points.

**2. What pgvector is.** Postgres normally can't store or compare vectors. The
**pgvector** extension adds a `vector` column type, math operators to measure how
close two vectors are, and special indexes to search them fast. Because it lives
*inside Supabase* (which we already run), we keep **one datastore** — no separate
Chroma to host or lose on a redeploy.

**3. How "closeness" is measured — cosine similarity.** We compare vectors by the
**angle** between them (cosine). Identical meaning → similarity ≈ 1.0; unrelated →
≈ 0. pgvector's `<=>` operator gives cosine *distance* (= 1 − similarity), so
"closest" = smallest distance. Our cache threshold of **0.95 similarity** means
"only reuse a cached answer if the new question is *almost the same meaning*."

**4. Why we store CHUNKS, not whole articles.** A 4-minute article covers several
ideas. If we embedded the whole thing as one vector, a question about *one* idea
would match weakly. So we split each article/post into **chunks** (a paragraph or
few) and embed each chunk. Retrieval then returns the *exact passage* that answers
the question — sharper matches, and less irrelevant text sent to the LLM (cheaper).
(The actual splitting is Phase 2.)

**5. Why the metadata columns (week, verdict, domain…).** Each chunk row carries
tags: `domain` (pregnancy/parenting), `week`/`trimester`, `category`, and
`verdict` (yes/moderation/avoid for "Can I…?" posts), plus `source_table`/
`source_id` to trace it back. These let us **filter** (only pregnancy chunks),
answer **safely** (surface the verdict explicitly), and **attribute** (link the
source). Metadata turns raw retrieval into *smart* retrieval.

**6. Why an index (HNSW).** Finding the closest vectors by checking every row is
fine for hundreds but slow as content grows. An **HNSW index** pre-builds a graph
of "who's near whom" so a search jumps to the nearest neighbours in a few hops
instead of scanning everything. (Same idea as a book's index vs reading every
page.) We add one on each embedding column.

**7. The three tables, and why each exists.**
- **veda_content_chunks** — the searchable knowledge base. Query = embed the
  question → find nearest chunks. The `unique(source_table, source_id, chunk_index)`
  means re-ingesting a changed article *updates* its chunks instead of duplicating.
- **veda_cache** — remembered Q&A. Two hit types: **exact** (`question_norm`,
  lowercased/stripped) and **semantic** (embedding within 0.95). A hit skips
  retrieval *and* the LLM → ~₹0. `week_key` is folded into the key so "X at week
  8" and "X at week 30" can't collide. `hit_count` surfaces FAQ demand; `last_used`
  lets us expire stale entries when content changes.
- **veda_usage_log** — one row per answer. Counting a user's rows today enforces
  the **rate limit**; summing today's `cost_usd` enforces the **daily spend cap**;
  averaging `cache_hit` gives the **cache-hit rate**. Cost control is measurable
  from day one, not guessed.

**8. `service_role` + RLS — why these tables are server-only.** These tables are
touched *only* by the AskVeda server, which connects with Supabase's
**service_role** key (a powerful, server-side key that bypasses row-level
security). We `enable row level security` and grant access **only** to
`service_role` — so the app's public/anon key can't read or write them at all.
(Contrast the content tables `articles`/`content_posts`, which are public-read for
the app.) This is the standard "secrets and machinery stay server-side" boundary.

### Your one action (Phase 1 handoff)
Run `sql/veda_setup.sql` in the Supabase SQL Editor, and grab the free
`service_role` key (dashboard → Settings → API) for our `.env`. Verify with the
two queries at the bottom of the SQL file.

### What Phase 2 adds
The **ingestion pipeline**: load an embedding model, split `articles` +
`content_posts` into chunks, embed each, and fill `veda_content_chunks`. That's
where *"how text becomes numbers"* stops being theory and you watch rows land.

---

## Phase 2 — Ingestion: how text becomes numbers

### What we built
- **`app/db.py`** — the Supabase client (connects as `service_role`), shared by
  every file that touches the database.
- **`app/embeddings.py`** — loads the embedding model once and exposes two
  functions: `embed_documents(list[str])` (for content) and `embed_query(str)`
  (for a user's question).
- **`ingest/chunker.py`** — `chunk_text()`, splits a body into embeddable pieces.
- **`ingest/ingest.py`** — the batch job: fetch published content → chunk → embed
  → upsert into `veda_content_chunks`. Run with `python -m ingest.ingest`.

Result of the first successful run: **19 chunks** (6 articles + 12 posts) embedded
to **384-dim** vectors and written to Supabase. Verified by counting rows in the DB.

### Concepts — how this actually works

**1. The embedding model is the thing that turns text → numbers.** In Phase 1 we
learned *what* a vector is; Phase 2 is *what produces it*. An **embedding model**
is a small neural network trained so that text with similar meaning comes out as
similar number-lists. Ours is **`bge-small-en-v1.5`** — "bge" is the model family,
"small" = the lightweight size, and it outputs **384** numbers per text (hence our
`vector(384)` column). We don't train it; we just *use* a pre-trained one.

**2. `fastembed` runs the model on your CPU — no PyTorch, no GPU.** Most ML
libraries drag in giant frameworks (PyTorch is ~2 GB) and want a GPU. `fastembed`
instead runs the model in the **ONNX runtime** — a lean engine for *running*
(never training) models on an ordinary CPU. That's exactly right for us: embedding
a few hundred short texts is light work, and it keeps the server small and cheap
to host. This is the whole reason we could stay "self-hosted, free, local."

**3. Where the model comes from — HuggingFace, downloaded once, cached forever.**
The model's files live on **HuggingFace** (the "app store for AI models"). The
*first* time you call the model, `fastembed` downloads it (~a few files) and
**caches it on disk**; every run after that loads instantly from the cache, fully
offline. So the download is a **one-time cost**, not a per-run one. (This is the
step your ISP blocked — see the war story below.)

**4. The SAME model must embed content *and* questions — non-negotiable.** A
vector only means something *relative to the model that made it*. If we embed the
articles with model A and later embed the user's question with model B, the two
number-lists live in different "meaning spaces" and comparing them is nonsense —
retrieval would return garbage. That's why `embeddings.py` is a single shared
module: ingest (now), query (Phase 3), and cache (Phase 4) all call the *exact
same* `bge-small-en-v1.5`. Same ruler for everything you measure.

**5. `query_embed` vs `embed` — a small but real detail.** bge-style models are
trained to embed a *question* slightly differently from a *document* (a short
query and a long passage aren't symmetric). So `embed_documents()` uses the
document path and `embed_query()` uses the query path. Using the right one on each
side gives noticeably better matches. (You'll see `embed_query` used in Phase 3.)

**6. Chunking in practice.** `chunk_text()` splits a body on **blank lines** (both
our plain-text articles and the Markdown posts are authored paragraph-by-
paragraph), then **greedily packs** paragraphs together up to ~**800 characters**
so chunks aren't tiny fragments or huge walls. Each resulting chunk becomes one row
(one embedding) in `veda_content_chunks`, tagged with its `chunk_index` (0,1,2…
within its source). Why it matters: sharp, passage-level retrieval and less text
sent to the LLM later (= cheaper answers). This is the Phase-1 theory made real.

**7. The ingest flow, end to end.**
```
fetch published rows  →  chunk each body  →  embed ALL chunks in one batch
       (Supabase)            (chunker)              (fastembed)
   →  attach each vector to its row  →  upsert into veda_content_chunks
```
- **Fetch**: `_fetch_published()` reads only `status='published'` rows, **paged**
  (1000 at a time) so it scales if content grows.
- **Embed in one batch**: we collect every chunk's text and embed them together in
  a single call, not one-by-one — far faster (the model processes them as a batch).
- **Idempotent upsert**: we `upsert(..., on_conflict="source_table,source_id,
  chunk_index")`. Because of the Phase-1 unique key, **re-running the ingest updates
  existing chunks instead of duplicating them.** That's why running it twice is
  safe — and why the count stayed correct (19, not 38) on re-runs.

**8. Why we hand pgvector a *string* like `"[0.12,0.98,...]"`.** The model gives us
a Python list of floats, but the data reaches Supabase through **PostgREST** (a
REST/JSON layer). The reliable way to get a list *into* a `vector` column through
that layer is pgvector's **text input format** — a bracketed, comma-separated
string. So `_to_vector_literal()` formats each vector as `"[0.123456,...]"` and
Postgres parses it back into a real vector on the way in. A small plumbing detail,
but it's *the* thing that makes "Python list → database vector" actually work.

**9. `service_role` reads the content tables it ingests.** The ingest job connects
as `service_role`. That key **bypasses RLS** but still needs an explicit table
**GRANT**, which is why Phase 1's SQL added `grant select on articles/content_posts
to service_role`. (This bit us with a `42501 permission denied` until the grant was
added — a clean illustration of "bypassing RLS ≠ having a grant.")

### War story — the HuggingFace download block (a real lesson)
The whole pipeline worked on the *first* try **except** the one-time model
download: `fastembed` fetching `bge-small` from HuggingFace failed with
`WinError 10054` (connection forcibly closed) — **0 bytes** through, even via a
mirror, while Supabase calls succeeded the whole time. Diagnosis: the ISP was
**blocking large HuggingFace file transfers specifically**, not the network in
general. The fix that worked: connect a **VPN** (Proton Free → Singapore) for the
*one* download; it fetched all 5 model files in ~15 seconds, cached them locally,
and now every future run works **offline, VPN off**. Lesson: a self-hosted model
is free and private, but it has a **one-time "get the model onto the machine"**
step — and hostile networks can block *that* even when normal API calls sail
through. (The alternative we weighed was a hosted embedding API, which sidesteps
the download entirely by turning it into a normal API call — worth remembering if a
machine ever can't get the file at all.)

### Verified
`select count(*) from veda_content_chunks` → **19**, with real titles/domains/weeks
(e.g. *"The anatomy scan, explained"*, pregnancy, week 20). The knowledge base is
real and queryable.

### What Phase 3 adds
The **retriever + the LLM**: embed a user's question with the *same* model
(`embed_query`), ask pgvector for the top-k nearest chunks, stuff them into a
grounded **prompt**, and send it to **Groq** (an open model) — producing a real
answer built *only* from your content. That's RAG's "R" and "G" finally meeting.

---

## Phase 3 — Retrieve → Prompt → Generate (the first real answer)

### What we built
- **`sql/veda_match_function.sql`** — a Postgres function `match_veda_content_chunks`
  that does the vector search (you run it once in Supabase).
- **`app/retriever.py`** — `retrieve(question)`: embed the question → call that
  function → return the closest chunks. (RAG's **R**.)
- **`app/prompt.py`** — `build_messages()`: the strict grounding rulebook + the
  retrieved chunks + the question, as `[system, user]` messages.
- **`app/llm.py`** — `complete()`: one thin wrapper around an OpenAI-compatible
  chat API, pointed at **Groq**. (RAG's **G**.)
- **`scripts/ask.py`** — a dev CLI to run all three steps and see each one.
- Config gained `llm_api_key`, `llm_base_url`, `llm_model`
  (`llama-3.1-8b-instant`), `llm_temperature` (0.2), `retrieval_top_k` (3).

The full path for one question:
```
QUESTION → embed_query() → rpc match_veda_content_chunks → top-k chunks
        → build_messages() (rules + chunks + question) → Groq LLM → grounded answer
```

### Concepts — how this actually works

**1. RAG = Retrieval-Augmented Generation, and now both halves exist.** *Retrieval*
finds the right passages from your content; *Generation* has the LLM write an
answer **using only those passages**. The "augmented" bit is the key: we don't ask
the model what it knows — we hand it your material and ask it to answer *from that*.

**2. Why a Postgres FUNCTION for search (the RPC).** Our code reaches Supabase via
**PostgREST** (REST/JSON). PostgREST can do "rows where status='published'" but it
**can't** express "order all rows by vector distance to *this* vector." So we wrap
that in a SQL function and call it by name with `supabase.rpc(...)`. The heart of
it is `order by embedding <=> query_embedding limit k` — `<=>` is pgvector's
**cosine-distance** operator (smaller = more similar), so it returns the k closest
chunks. It also returns `similarity = 1 - distance` (1.0 = identical meaning) so we
can *see* match quality. Bonus: the math runs **inside the DB using the HNSW
index** from Phase 1 — fast, and it never drags all rows into Python.

**3. The same-model rule shows up again.** `retrieve()` embeds the question with
`embed_query()` — the **same** `bge-small` used at ingest. If it weren't, the
question's vector and the chunk vectors would live in different spaces and the
`<=>` comparison would be meaningless. (This is why `embeddings.py` is shared.)

**4. Grounding is a PROMPT, not magic.** `prompt.py` is where trust is enforced.
The **system message** is a strict rulebook: *use ONLY the provided content; if
it's not there, say so — never invent; state the verdict for "can I…?" questions;
be warm and concise; route possible emergencies to a doctor calmly, no alarm.* The
**user message** carries the retrieved chunks (labelled with title/week/verdict)
then the question. An ungrounded LLM is a confident guesser; grounding turns it
into *a reader of your material*. This single file is the difference between "safe"
and "makes things up."

**5. Why the OpenAI-compatible client (provider independence).** `llm.py` uses the
generic `openai` library, not a Groq SDK. Groq/Together/OpenAI all speak the same
API shape, so pointing `base_url` at Groq is all it takes. Switching provider or
model later = change two config values, **zero code**. That's a deliberate hedge
against lock-in and price changes.

**6. Low temperature = factual, not creative.** `temperature=0.2` keeps answers
consistent and grounded. High temperature adds randomness/flair — the opposite of
what a health assistant should do.

**7. We already capture tokens (for Phase 4).** `complete()` returns
`input_tokens`/`output_tokens`, not just text. Cost = tokens × price, so returning
them now means Phase 4's per-answer cost logging + daily spend cap just plug in.
(In the tests: ~800–2700 input tokens, tens of output tokens — fractions of a
paisa per answer.)

### Verified — three behaviours, the real proof
Run via `python -m scripts.ask "…"`:
- **Grounded hit** — *"what happens at the anatomy scan?"* → retrieved the week-20
  "anatomy scan" article (similarity **0.73**) and answered from it (organs,
  placenta, fluid, cord, optional sex reveal). ✅
- **Honest miss** — *"best stroller brand to buy?"* → only weak matches (~0.5), and
  the model said *"I don't have information about stroller brands in the content
  provided"* instead of inventing one. ✅ **This is the critical test.**
- **Verdict** — *"can I eat papaya during pregnancy?"* → found the exact "Can I eat
  papaya?" post (**0.76**) and gave the nuanced verdict (ripe in moderation, avoid
  raw/unripe). ✅

### What Phase 4 adds
**Cache + guardrails.** Before spending an LLM call we'll check `veda_cache`
(exact-match, then semantic ≥0.95) so repeat questions return instantly for ~₹0;
and we'll add the safety rails — scope check (pregnancy/parenting only), red-flag
routing, the 20/day per-user rate limit, and the global daily spend cap — logging
every call to `veda_usage_log` so cost is measurable from day one.

---

## Phase 4 — Cache + guardrails (make it cheap & safe)

### What we built
- **`sql/veda_cache_function.sql`** — `match_veda_cache`, the semantic-cache vector
  search (you run it once in Supabase).
- **`app/usage.py`** — the money meter: `log_usage()`, `count_today()`,
  `spend_today()` over `veda_usage_log`.
- **`app/guardrails.py`** — `red_flag_response()`, `within_rate_limit()`,
  `within_spend_cap()`.
- **`app/cache.py`** — `lookup()` (exact → semantic), `store()`, `normalize()`,
  `week_key_for()`, exact-only detection.
- **`app/answer.py`** — **the brain**: one `answer()` that wires guardrails → cache
  → retrieval → LLM → logging in the right order. This is the single code path both
  doors (app, WhatsApp) will call.
- Refactored `retriever.py` to accept a pre-computed `q_vector` (embed once, reuse).
- Config gained the thresholds/limits/prices; `scripts/ask.py` now runs the full brain.

### Concepts — how this actually works

**1. Order is a cost-and-safety strategy.** `answer()` runs its checks
**cheapest/safest first, the paid LLM call last**:
```
rate limit → spend cap → red-flag → cache → retrieve → confidence floor → LLM → store+log
```
Every step that can end the request *without* an LLM call is placed *before* it. A
throttled user, an emergency, a cached question, or an off-topic query all resolve
for ~₹0. You only pay at the very end, and only if nothing cheaper applied.

**2. The cache has two doors — exact and semantic.**
- **Exact**: we `normalize()` the question (lowercase, strip punctuation, collapse
  spaces) and look it up on `(question_norm, week_key)`. Zero AI — a plain key lookup.
- **Semantic**: a *reworded* question ("what happens **during** the anatomy scan"
  vs "**at** the anatomy scan") won't match exactly, so we embed it and ask
  `match_veda_cache` for a cached question within **0.95** similarity. This is why
  the cache needs a vector function, just like retrieval.
A hit skips **both** retrieval and the LLM. (Verified live: the paraphrase came back
as `cache:semantic`, and the cached row's `hit_count` reached 2.)

**3. `week_key` = cache safety.** "Is it safe at week 8?" and "…at week 30?" embed
almost identically but need *different* answers. So the week/trimester is folded
into the cache key — same wording in a different week bucket is a different entry.
Never serve the wrong-week answer.

**4. Verdict/dosage questions are exact-only.** For "can I…?" / dosage questions, a
tiny wording change can flip the correct answer. So `_is_exact_only()` disables
*semantic* reuse for them — they may only be served an identical repeat, never a
merely-similar one. Grounding safety extended into the cache.

**5. Embed once.** The question's embedding is needed by both the cache lookup and
retrieval. `answer()` computes it a single time and passes it into both (that's why
`retrieve()` now takes an optional `q_vector`). Small change, real saving — we never
run the model twice for one question.

**6. Guardrails, plainly.**
- **Red-flag routing**: a keyword scan for emergency phrases (heavy bleeding, severe
  pain, reduced movement, water broke, …). On a match we return a **calm** "please
  see your doctor" and skip RAG entirely. Deliberately broad — a false positive just
  adds a gentle nudge, a safe way to be wrong for a health app. **No alarm styling.**
- **Rate limit**: `count_today(user)` vs 20 — anti-spam + cost control.
- **Spend cap**: `spend_today()` (sum of `cost_usd`) vs the daily cap — a global
  **circuit breaker** so the bill *cannot* run away even under abuse.

**7. The confidence floor — and an honest limitation.** After retrieval, if the best
chunk is below `min_retrieval_similarity` (0.30), we decline *without* paying the
LLM. It's a **cost optimization**, not the main safety net. In testing, "who won the
cricket match?" scored **0.414** — above 0.30 — so it slipped past the floor and did
cost a tiny LLM call ($0.000036)… but the **grounding backstop** caught it anyway
("I don't have information about a cricket match"). Lesson: it's a **two-layer
defense** — a cheap floor to short-circuit the obvious, and grounding to catch the
rest. The floor is a **tunable knob**: raise it → skip more off-topic for free, but
risk rejecting valid-but-distant questions. We kept it conservative on purpose.

**8. `veda_usage_log` = honesty from day one.** Every answered path writes a row
(cache hits at $0, LLM calls with real token counts + computed cost). Cost =
`tokens × per-million price` (config). On the **free tier nothing is actually
charged**, but we *measure* as if it were — so the rate limit, the spend cap, and
future cache-hit-rate reports all work on real numbers, not guesses.

**9. `answer.py` is "one brain, two doors" made concrete.** Everything above lives in
ONE function. Phase 5's app endpoint and Phase 6's WhatsApp webhook will just
normalize their input and call `answer(...)`. No RAG logic will ever live in a
channel — this file *is* the brain.

### Verified — five paths, one run
Via `python -m scripts.ask "…"`:
| Question | Expected path | Result |
|---|---|---|
| "what happens at the anatomy scan?" | fresh LLM | `source=llm`, $0.000142, 7.9s |
| same, again | exact cache | `source=cache:exact`, $0, no tokens |
| "…**during** the anatomy scan?" | semantic cache | `source=cache:semantic`, $0 ✅ |
| "I have heavy bleeding at week 12" | red-flag | calm doctor routing, no RAG |
| "who won the cricket match?" | decline | grounding said "I don't have that" |

DB after: `veda_cache` hit_count=2 on the reused question; `veda_usage_log` = 5 rows
with matching costs. (Cosmetic note: the terminal shows `�` where the answer uses an
em-dash — that's just Windows console encoding; the stored/served text is correct.)

### What Phase 5 adds
**The app door.** Wrap `answer()` in a `POST /ask` HTTP endpoint (in `main.py` via
`channels/app_api.py`), verify the app user's Supabase login (JWT) to get their
`user_key`, and return the answer as JSON. Then the *existing* in-app Ask Veda screen
calls it — and you see a grounded answer inside the app, not just the terminal.

---

## Phase 5 — The app door (POST /ask) + wiring the app screen

### What we built
**Backend (this repo):**
- **`app/auth.py`** — `resolve_user_key()`: verify the app's Supabase login token
  (JWT) → the user id; in dev, fall back to a dev key so we can test now.
- **`app/channels/app_api.py`** — the `POST /ask` route (request/response models),
  which just resolves the user and calls `answer()`.
- **`app/main.py`** — mounts that router + dev CORS. Config gained
  `supabase_jwt_secret` + `require_auth`. Added `pyjwt`.

**App side (the Flutter repo, `parentveda`):**
- **`lib/ask_veda_config.dart`** — the base URL (a `static const`, like SupabaseConfig).
- **`lib/services/remote/ask_veda_service.dart`** — `AskVedaService.ask()`: POSTs the
  question, returns a result or **null** on any failure.
- Wired **`lib/screens/post_pregnancy/askveda_screen.dart`** (the parenting Ask Veda
  screen) to call it and swap in the grounded answer. Added `http`.

### Concepts — how this actually works

**1. An endpoint is a door onto the brain — nothing more.** `POST /ask` does three
tiny things: figure out *who's asking* (auth → user_key), call the ONE `answer()`
brain, return its result as JSON. There is **no RAG logic in the channel** — that
all lives in `answer()`. This is the payoff of Phase 4's design: the door is ~20
lines because the brain already exists.

**2. Auth turns a request into a `user_key`.** The rate limit and personalization
need to know *who* is asking. In production the app sends its Supabase login as
`Authorization: Bearer <jwt>`; we verify it with the project's JWT secret and read
`sub` (the user id). In local dev — where the app's Supabase auth may not be wired
yet — we **fall back** to a dev key (or an `X-User-Key` header) so the whole path is
testable today. `require_auth=true` (production) turns the fallback off.

**3. CORS, in one line.** A *browser* refuses to call a server on a different origin
unless the server says it's allowed — that's CORS. So Flutter **web** would be
blocked without it; native mobile ignores CORS entirely. We allow everything in
local dev and would lock it to real origins in production.

**4. The app integration pattern: offline-first, then augment.** The screen was
fully offline (instant). We kept that exactly, and *added* a background call:
- On submit, show the **offline** answer immediately (all 7 sections, zero latency).
- Fire `AskVedaService.ask()` in the background.
- If it returns a **confident** answer (a real generation, a cache hit, or a
  red-flag safety routing), **swap it into the "Veda Answer" section** with a small
  verified tick.
- If it fails (server down, no network) or returns **low-confidence**, keep the
  offline answer. The service returns **null** on any error, so the app *cannot
  break* because of it — it only gets smarter when the backend is reachable.

**5. Why `isConfident` matters.** Our content is still small, so for many questions
the backend honestly says "I don't have that." We must NOT replace a good offline
answer with that decline — so we only swap in when `source` is `llm` / `cache:*` /
`red_flag`. As content grows, the grounded answer wins more often. (There's also a
**stale-response guard**: if the user has moved on to another question, a late reply
for the old one is ignored.)

**6. The base-URL reality (dev networking).** "localhost" means different things to
different runtimes: an **Android emulator** reaches the host PC at **`10.0.2.2`**, an
**iOS simulator/desktop** at `127.0.0.1`, and a **physical phone** needs the PC's
**LAN IP**. So the base URL is a single, clearly-commented const to flip per setup.
In production it becomes the deployed HTTPS URL (Phase 9).

**7. "One brain, two doors" — the first door is real.** `app_api.py` is door #1.
Phase 6's WhatsApp webhook will be door #2, calling the *same* `answer()`. Neither
contains any AI logic.

### Verified
- `POST /ask` over HTTP (curl): a content question → `source: llm`; a repeat →
  `source: cache:exact, cache_hit: true`; an `X-User-Key` header flows to the rate
  limiter. `/health` still green.
- App side: `flutter analyze` clean on the changed files; **all 173 app tests pass**
  (including the widget test that submits on this screen — the async call degrades
  gracefully to the offline answer).

### Both screens done
The **pregnancy flagship** (`ask_veda_screen.dart`, 1700 lines) is now wired too.
The trick that kept it surgical: in *all three* of its answer paths (showcase /
retrieval / honest-fallback) the S1 "Veda Answer" card is always `children[0]`, so
we swap **only that one slot** with the grounded answer and leave every other
section untouched. It calls the backend with `domain: 'pregnancy'` + the user's
`week`, so it actually lights up (the backend's content is pregnancy). `flutter
analyze` clean; all Ask Veda tests green.

---

## One mother, one journey — the architecture correction

This batch came from a defect *you* spotted while testing, and it changed the
design more than any phase so far. Worth understanding properly.

### The problem
On the **parenting** screen, asking "what happens at the anatomy scan?" first
showed *"I don't have a confident answer"* and then a grounded pregnancy answer —
addressed to a mother whose baby is **4 months old**. Two things were wrong:

1. **Amnesia.** The plan had been to add a `domain: 'parenting'` filter so the
   parenting side only saw parenting content. But that means an assistant that
   answered her scan questions for nine months suddenly claims not to know what a
   scan is the moment her baby arrives. Absurd.
2. **Inconsistency.** The same question produced a *seeded* answer on the
   pregnancy side and a *RAG* answer on the parenting side. **Two sources of truth
   for one question** — worse than either answer alone.

### The decisions
- **No domain gating, ever.** The mother is ONE person on ONE continuous journey.
  Both doors reach the whole database. "Focused on parenting" ≠ "incapable of
  pregnancy."
- **The seeded `kVedaShowcase` answers were prototypes**, written early just to see
  how the 7-section layout would look — not curated truth. So they were demoted
  from hardwired answers to **ordinary content pieces** (now ingested like anything
  else), and answers come from **one source: content (RAG) + cache**, uniformly.
- **Stage is CONTEXT, not a gate.** Instead of blocking pregnancy content from a
  postpartum mother, we *tell the model where she is* and let it phrase things
  correctly. Full knowledge, right framing.

### Concepts — how this actually works

**1. Stage as context (the tense fix).** `describe_stage()` produces a line like
*"She is NO LONGER PREGNANT — her baby is 4 months old. Her whole pregnancy is in
the PAST…"*, and a prompt rule demands the matching tense. Results:
- 4 months postpartum → *"**During your pregnancy**, the anatomy scan **checked**…"*
- Week 12 → *"you're still **a bit ahead of** the anatomy scan… **here's what
  happens**"* — and it still hands over the full information.

**Practical lesson:** the first version of that instruction was too polite, and the
8B model **dodged into passive voice** ("your baby's growth *is checked*") to avoid
committing to a tense. It only complied once the instruction was blunt, with an
example, plus an explicit *"do not use vague passive phrasing to dodge the tense."*
Small models follow forceful, exemplified instructions — not gentle ones.

**2. The stage-bucketed cache — personalization vs. cost.** These two pull against
each other: the more an answer is tailored to one mother, the less it can be
reused, and cost climbs. The resolution:
- Cache the **grounded core** (facts everyone at that stage shares).
- Key it by **stage bucket** — `pw24` (pregnancy week 24), `cm3` (child 3 months).
- Apply personal framing **at the edges** (her name, her week), never baked into
  the cached text.

Take *"can we have sex during pregnancy?"* — the core answer is the same, but the
caveats differ by trimester. With a stage-keyed cache, T1 and T3 get **separate,
each-correct** cached answers, and every mother at that stage shares one. Correct
*and* cheap — no trade-off.

**3. Never cache what's hers alone.** `is_personal()` catches "my report", "my
weight", "my scan"… Those bypass the cache entirely. Serving one mother's answer to
another would be both wrong and a privacy smell.

**4. Gap logging + the `NO_ANSWER` sentinel.** Every question we *can't* answer is a
piece of content waiting to be written — so it goes to `veda_content_gaps`, where
repeats bump `ask_count`. One query gives a demand-ranked content to-do list.
The clever bit: rather than guessing from prose whether the model gave up, we
**instruct it to emit exactly `NO_ANSWER`**. A structural signal is reliable where
string-matching apologies is not — and it means we never cache a decline.

**5. The content migration — one database for both sides.** The parenting side had
*nothing* to ground on because its knowledge lived in **Dart files**, not Supabase.
So: `tool/export_veda_corpus.dart` (in the app repo, run via `flutter test` because
the corpora pull in Flutter) dumps the corpus — built **twice, once per language**,
merged by doc id, since `VedaDoc` is monolingual — to JSON. `ingest/import_corpus.py`
loads it into a **new `veda_knowledge` table**, deliberately separate from
`articles`/`content_posts` so a re-export can never clobber editor-authored content.

Result: **19 chunks → 927** (831 pregnancy, 83 parenting). *"When should I start
solids?"* now grounds at **0.787** — it had nothing this morning.

Two deliberate limits: **community is excluded** (opinions must never source an
answer — a standing product rule), and **Hinglish is stored but not embedded**,
because `bge-small-en` is English-only and mixing languages in one index degrades
retrieval. Every chunk keeps its `kind`, so any category (the 446 `spiritual` docs
are 49% of the corpus) can be filtered later with one line — no re-export.

**6. The honest debt.** The app now holds its knowledge in **two places**: the Dart
files (offline) and Supabase (RAG). They can drift. The real fix is the app reading
everything from Supabase — a bigger refactor, deliberately deferred.

---

## Phase 6 — The WhatsApp door

### What we built
- **`app/gateway.py`** — `IncomingMessage` (the one internal shape) +
  `parse_msg91_inbound()` which turns a provider payload into it.
- **`app/channels/whatsapp.py`** — `POST /whatsapp/webhook` + `send_whatsapp_message()`
  (mock by default until the number is live).
- **`main.py`** now mounts BOTH doors. Config gained `msg91_*` + `whatsapp_mock_send`.

### Concepts — how this actually works

**1. What a webhook is (and why WhatsApp needs one).** With the app, *we* get
asked: the app calls our `/ask` and waits. WhatsApp is the reverse — a mother texts
whenever she likes, and **something must already be listening**. A **webhook** is
just "a URL you give someone else so they can call *you* when an event happens."
We hand MSG91 our `/whatsapp/webhook` URL; every inbound message arrives as an HTTP
POST. This is *why* AskVeda must be an always-on server: no Flutter app is running
at 2am when she texts.

**2. What MSG91 actually is (the BSP).** You cannot talk to WhatsApp directly —
Meta only exposes the WhatsApp Business API through approved **BSPs** (Business
Solution Providers). MSG91 is ours. The chain:
```
Mother's WhatsApp ──► Meta ──► MSG91 (BSP) ──► our webhook ──► answer()
                                    ▲                              │
                                    └──────── our reply ───────────┘
```
MSG91 handles number provisioning, Meta compliance and delivery; we handle the
thinking. (Chosen because it's API-driven with no dashboard lock-in or markup.)

**3. The gateway — where "two doors" becomes literal.** The app sends clean JSON;
MSG91 sends a provider-shaped payload. The gateway converts **both** into one
`IncomingMessage(text, user_key, channel, reply_to, …)`. That's the only shape
`answer()` ever sees, so **zero AI logic lives in either channel file**. Adding SMS
or a web widget later means growing this one file, nothing else.

**4. Two webhook rules that bite people.**
- **Always return 200, fast.** If you error or hang, the provider *retries* — and
  the mother receives the same answer three times. Even our unauthorized and
  failure paths return 200.
- **Ignore non-message events.** Delivery receipts, read receipts and status
  callbacks all arrive at the *same* URL. `parse_msg91_inbound()` returns `None`
  for those, and we skip them. Answering a delivery receipt would be absurd — and
  would cost money.

**5. Identity without an account.** On the app, `user_key` comes from her Supabase
login. On WhatsApp there's no login — just a phone number — so the key is
`wa:<phone>`. That's enough for the rate limit and usage log to work identically on
both channels.

**6. Webhook security.** The URL is public, so anyone could POST fake messages and
burn your LLM budget. We require a shared secret header (`msg91_webhook_secret`),
configured on both sides. It's only enforced once set, so local testing stays easy.

**7. Mock-first.** `whatsapp_mock_send=True` logs the reply instead of calling
MSG91, so the **entire path is testable before the account exists**. The live call
is written but must be confirmed against MSG91's docs when the number is real —
provider payload shapes are exactly the kind of thing you should verify, not
assume. (The inbound parser accepts several plausible shapes for the same reason;
narrow it once a real payload is seen.)

**8. Why WhatsApp replies are effectively free.** Meta bills *business-initiated*
template messages, but when a user messages **first** it opens a **24-hour customer
service window** in which replies are free. AskVeda is always user-initiated — so
the conversation cost is ~0, and our only cost is the LLM (and often not even that,
thanks to the cache).

### Verified (on simulated payloads, no MSG91 account needed)
| Test | Result |
|---|---|
| A real question `{"from":"91…","content":{"text":"when should I start solids?"}}` | answered + reply sent to that number ✅ |
| A **delivery receipt** `{"type":"status",…}` | `{"status":"ignored"}` — not answered ✅ |
| A **different payload shape** (`message.text.body`) | parsed and answered ✅ |

Both answers returned **`cache:exact`** — they'd been asked earlier through the CLI,
so the WhatsApp users got them instantly for ~₹0. **The cache is shared across
channels**: one mother's question makes the answer free for everyone, on every door.

### Known limitation (deliberate)
WhatsApp gives us a **phone number, not an app profile**, so we don't know her week
or her baby's age — she gets correct answers, but without the stage-aware tense
framing the app enjoys. Once phone↔profile linking exists we pass the same stage
fields and both doors behave identically. The plumbing already accepts them.

## Phase 7 — Trusted-web fallback + the content flywheel

### What we built
- **`app/web_fallback.py`** — `search_trusted()`: search a **whitelist** of health
  authorities (via Tavily, which does search + clean extraction in one call).
- **`app/flywheel.py`** — `draft_from_gap()`: write the answer to `veda_drafts`
  for a human to review.
- **`sql/veda_drafts.sql`** — the review inbox (question, draft body, **source
  URLs**, status `pending`).
- **`prompt.build_web_messages()`** — grounds on the fetched passages and requires
  the answer to name its source.
- **`answer.py`** — both gap paths (confidence floor *and* `NO_ANSWER`) now try the
  web before dead-ending. Config gained the whitelist + toggles.

### Concepts — how this actually works

**1. The whitelist IS the safety mechanism.** We never search the open web. A
mommy-blog or forum can be wrong in ways that genuinely matter here, so only
hand-approved authorities are consulted: NHS, ACOG, WHO, Mayo, AAP,
HealthyChildren + Indian NHP, ICMR, FOGSI. Tavily's `include_domains` is literally
a whitelist parameter, which is why it fits.

**2. The loop that makes failures into assets.**
```
gap → answer her from a trusted source (never dead-ended)
    → log the gap (ask_count++)
    → write a DRAFT for an editor
    → editor publishes it as real ParentVeda content
    → the NEXT mother gets a cheap, grounded, OWNED answer
```
**The expensive path is self-extinguishing**: the more it fires, the less it needs
to. We also cache the web answer, so even before an editor acts, the *second*
asker at that stage gets it for ~₹0.

**3. We never auto-publish medical content.** Machine-written text lands in
`veda_drafts` as `pending`, **with its source URLs**, for a human to verify.
Publishing stays a deliberate human act.

**4. Why drafts get their own table** (not `articles` with `status='draft'`):
safety (unreviewed medical text must not sit where a mis-click publishes it),
ownership (`articles` belongs to the editorial/Directus workflow), and audit (the
articles schema has nowhere to keep source URLs). Register `veda_drafts` in
Directus and it becomes an "AI drafts" inbox.

**5. Degrade, never break.** With no Tavily key the fallback returns nothing and we
show the same honest decline as before. Every new capability is added so its
absence is harmless.

### Two prompt lessons from testing (both real bugs we caught)
- **Never assume a stage.** With no stage passed, the model opened with *"Since
  you've already had your baby…"* — it **invented** one. Telling a pregnant mother
  she's given birth would be genuinely upsetting. Fixed with an explicit rule: if
  you aren't told her stage, don't assume or mention one.
- **Don't over-apply the tense rule.** Once told she was postpartum, the model
  dragged pregnancy into a *stroller* question ("during your pregnancy you likely
  researched…"). Fixed by scoping it: only bring up her pregnancy if the question
  is actually about pregnancy; otherwise just answer normally for a parent of a
  4-month-old.

Both are the same underlying lesson from Phase 6's tense work: **small models apply
instructions bluntly, so instructions need explicit scope and counter-examples.**

### Three bugs found by actually running it (all worth keeping in mind)

**a) Gap detection didn't work.** Asking *"what is obstetric cholestasis?"* produced
*"At around week 30 you'll have had a growth scan… however the content doesn't
mention obstetric cholestasis."* — padding, then a shrug. The `NO_ANSWER` sentinel
was ignored by the model perhaps half the time, so the gap was never logged and the
fallback never fired. Fix: `_is_no_answer()` now has **three** detectors — the
sentinel, the sentinel buried after preamble, and the **prose forms** ("the content
doesn't mention/cover/include…"). *Ask for a clean signal, but detect what the model
actually does.*

**b) Conversational queries wreck web search.** *"what is obstetric cholestasis?"*
returned **YouTube, Wiktionary and WhatsApp** — the engine latched onto "what".
The same search as *"obstetric cholestasis"* returned exactly the right NHS
leaflets. Fix: `_to_search_query()` strips question lead-ins before searching.
**A search engine wants keywords; an LLM wants a sentence. Don't feed one the
other's input.**

**c) The provider's whitelist is not a guarantee.** In that same broken call,
`include_domains` was set to our nine health authorities and Tavily *still*
returned youtube.com and whatsapp.com. For a health assistant, grounding an answer
on an unvetted page is the one failure we can't allow. Fix: `_host_allowed()`
re-checks every returned URL's hostname against the whitelist and drops anything
else. **The provider's filter is a request; our own check is the guarantee.**

### Verified end to end
*"what is obstetric cholestasis?"* (nothing in our content) →
```
source: web   sources: bhrhospitals.nhs.uk, fogsi.org, buckshealthcare.nhs.uk
"At around week 30 of your pregnancy, obstetric cholestasis is a liver disorder…
 unexplained itching, usually on your palms and soles, worse at night…
 Source: [1] BHR Hospitals NHS, [2] FOGSI-Insights 2021, [3] Bucks Healthcare NHS"
```
Stage-aware, accurate, **cites its sources**, and costs ~$0.000065 ≈ **₹0.006**.

### Still needed
`sql/veda_drafts.sql` run in Supabase — without it the answer still works, but the
draft can't be saved for editorial review (it fails gracefully and logs).

## Phase 8 — Reindex, benchmark, observe

### What we built
- Refactored **`ingest/ingest.py`** into shared pieces + `reindex_source(table, id)`
  (re-embed ONE row) alongside `ingest()` (everything).
- **`app/channels/admin.py`** — `POST /reindex` and `GET /metrics` (secret-guarded).
- **`app/metrics.py`** — cache-hit rate, cost, gaps, FAQs from the logs.
- **`scripts/metrics.py`** (report) and **`scripts/benchmark.py`** (model compare).
- Config gained `reindex_secret`.

### Concepts — how this actually works

**1. `POST /reindex` closes the flywheel loop.** Until now, embedding only happened
when *we* ran the ingest by hand — so a draft published in Directus sat in Supabase
**invisible to search**. Now Directus fires a webhook here on publish/edit and we
re-embed. Two properties matter:
- **Incremental**: publish one article → we delete just its old chunks and re-embed
  just that row (`reindex_source`), not all ~900. Deleting first also handles a body
  that shrank, or a row that got unpublished (its chunks simply vanish from search).
- **Full = background**: a whole re-embed takes minutes, so `/reindex` with no body
  kicks it off with FastAPI `BackgroundTasks` and returns immediately. A webhook that
  waited would time out and the provider would retry it.
The endpoint accepts both our `{source_table, source_id}` and Directus's native
`{collection, keys:[...]}`, so wiring is just: Directus Flow → Webhook → this URL.

**2. Benchmark — decide the model with evidence.** `scripts/benchmark.py` runs the
SAME grounded prompt through several Groq models and prints latency + tokens + cost
+ the actual answer, so quality is judged by eye. It bypasses the cache (we're
measuring the models, not the cache). Findings on 4 real questions:
| model | latency | cost/answer | note |
|---|---|---|---|
| **llama-3.1-8b-instant** | 0.3–1.3s | **~₹0.004** | concise, accurate — our default |
| openai/gpt-oss-20b | 0.7–0.9s | ~₹0.02 | more detail but verbose; can over-add |
| llama-3.3-70b-versatile | 0.4–0.6s | ~₹0.05 | rich, high quality — premium swap |
| qwen/qwen3.6-27b | 4.7s | ~₹0.12 | a REASONING model, dumps `<think>` — dropped |
**Kept the 8B.** For a grounded system the *content* carries the facts, so a small
concise model is ideal — and cheapest. A telling detail: the more verbose models
sometimes **added specifics not in the content** (a grounding risk). More words =
more room to drift. Switching model later is one config line.

**3. Observe — make cost and quality visible.** `veda_usage_log` was capturing every
answer; `metrics.py` reads it into the numbers that matter:
- **cache-hit rate** — the single biggest cost lever
- **cost per answer** + **today's spend vs the cap**
- **web-fallback rate** — how often our own content falls short
- **top gaps** (write these next) and **top cached** (the real FAQs)
Our own testing so far: 31 answers, **25.8% cache-hit**, total **₹0.09**, and the #1
gap is "what is obstetric cholestasis?" (asked 2×) — the content to-do list building
itself, exactly as designed.

### Verified
- `reindex_source` re-embeds one row; unknown table guarded; a missing/unpublished
  id correctly removes its chunks. `POST /reindex` works for full (background),
  incremental, and the Directus `{collection, keys}` shape.
- `GET /metrics` and `python -m scripts.metrics` return the live report.
- `scripts/benchmark.py` ran across three models cleanly.

### To wire Directus (when you set it up)
Create a **Flow** on your content collections → **Webhook** action → `POST` to
`<service>/reindex` with body `{"collection":"{{collection}}","keys":{{keys}}}` and
header `x-reindex-secret: <REINDEX_SECRET>`. Publishing then refreshes search
automatically.

### What Phase 9 adds
**Deploy.** Put the service on Render (always-on, ~₹600/mo), set the real env vars +
secrets, point the app's `AskVedaConfig.baseUrl` and MSG91's webhook at the public
HTTPS URL. After that, all the dev-only ceremony (adb reverse, localhost) disappears
and it works for real users on real phones, anywhere.
