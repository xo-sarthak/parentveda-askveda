"""
The embedding model — turns text into a 384-number vector (its meaning).

We use `fastembed`: it runs the model on ONNX (a lightweight runtime), so there's
NO heavy PyTorch install and it stays fast + small on a plain CPU — ideal for a
cheap always-on host. The SAME model embeds both our content AND, later, the
user's question — so their vectors live in the same "meaning space" and can be
compared.

bge models want a tiny instruction added to the *question* (not the content) for
best retrieval — fastembed's `query_embed()` handles that for us automatically.
"""

from functools import lru_cache

from fastembed import TextEmbedding

from app.config import settings


@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    # Loaded once and reused. The FIRST call downloads the model files (~small).
    return TextEmbedding(model_name=settings.embed_model)


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed content chunks (passages) — used during ingestion."""
    return [vec.tolist() for vec in _model().embed(texts)]


def embed_query(text: str) -> list[float]:
    """Embed a user's question (adds bge's retrieval instruction) — used at query time."""
    return list(_model().query_embed(text))[0].tolist()
