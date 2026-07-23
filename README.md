# AskVeda — RAG chatbot backend

Grounded pregnancy/parenting Q&A on two channels (the ParentVeda app + WhatsApp).
**One brain, two doors:** the RAG logic lives only in this service; the app and
WhatsApp are just two entry points onto the same code path.

- **Architecture + deep, build-along learning notes:** [`askveda.md`](./askveda.md)
- **Shares (never touches):** the ParentVeda Supabase DB + Directus. Its own repo.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (mac/linux: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open **http://localhost:8000/health** → `{"status":"ok",...}`
(Interactive API docs are auto-generated at **http://localhost:8000/docs**.)
