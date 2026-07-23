"""
Chunking — split a long body into small, embeddable pieces.

Why: a whole article covers several ideas; embedded as one vector it matches any
single question only weakly. Small chunks → sharper retrieval AND less text sent
to the LLM (cheaper). We split on blank lines (both our plain-text articles and
the Markdown posts are authored paragraph-by-paragraph), then greedily pack
paragraphs up to ~max_chars so chunks aren't tiny or huge.
"""


def chunk_text(text: str, max_chars: int = 800) -> list[str]:
    paragraphs = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    chunks: list[str] = []
    buffer = ""
    for para in paragraphs:
        # If adding this paragraph would overflow, close the current chunk first.
        if buffer and len(buffer) + len(para) + 2 > max_chars:
            chunks.append(buffer)
            buffer = para
        else:
            buffer = f"{buffer}\n\n{para}" if buffer else para
    if buffer:
        chunks.append(buffer)
    return chunks
