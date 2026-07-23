"""
Retriever (Phase 3) — the "R" in RAG.

Turn a user's QUESTION into the handful of content chunks most likely to answer
it. Flow: embed the question with the SAME model used at ingest → ask the database
(via the match_veda_content_chunks function) for the nearest chunks by cosine
similarity → return them (with their metadata) for the prompt builder to use.
"""

from app.config import settings
from app.db import supabase
from app.embeddings import embed_query


def retrieve(
    question: str,
    top_k: int | None = None,
    domain: str | None = None,
    q_vector: list[float] | None = None,
) -> list[dict]:
    """Return the top-k content chunks closest in meaning to `question`.

    Each returned dict includes the chunk text + metadata (title, week, verdict…)
    and a `similarity` score in [0, 1] (higher = closer). `domain` optionally
    restricts to 'pregnancy' or 'parenting'. Pass `q_vector` to reuse an already
    computed question embedding (the cache step embeds it first — no double work).
    """
    top_k = top_k or settings.retrieval_top_k

    # Embed the question (unless the caller already did). query_embed uses bge's
    # QUERY path, tuned for short questions vs long passages — better matches.
    if q_vector is None:
        q_vector = embed_query(question)

    # Call the Postgres function. supabase-py JSON-encodes the params; the float
    # list serializes to pgvector's own text form ("[0.1,0.2,...]"), so it lands
    # in the vector(384) parameter cleanly.
    res = supabase.rpc(
        "match_veda_content_chunks",
        {
            "query_embedding": q_vector,
            "match_count": top_k,
            "filter_domain": domain,
        },
    ).execute()

    return res.data or []
