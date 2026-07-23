"""
Trusted-web fallback (Phase 7) — so AskVeda never dead-ends.

When OUR content can't answer a genuine pregnancy/parenting question, we may
answer from a WHITELIST of authoritative medical sources. The whitelist IS the
safety mechanism: a mommy-blog or forum could be wrong in ways that matter here,
so only hand-approved health authorities are ever consulted.

We use Tavily because it does search AND clean content extraction in one call
(purpose-built for RAG), and supports `include_domains` — which is exactly a
whitelist. Without an API key this module simply returns nothing and the caller
falls back to the honest decline, so nothing breaks.
"""

from urllib.parse import urlparse

import httpx

from app.config import settings


def trusted_domains() -> list[str]:
    return [d.strip() for d in settings.web_trusted_domains.split(",") if d.strip()]


# Conversational lead-ins wreck web search. Asking "what is obstetric cholestasis?"
# made the engine latch onto "what" and return YouTube/Wiktionary/WhatsApp; asking
# "obstetric cholestasis" returned exactly the right NHS leaflets. A search engine
# wants KEYWORDS, not a sentence — so we strip the question framing.
_LEAD_INS = (
    "what is", "what are", "what's", "what does", "what do",
    "how do i", "how can i", "how does", "how is", "how much", "how many",
    "can i", "is it safe to", "is it ok to", "is it okay to", "should i",
    "when should i", "when do i", "why is", "why do", "why does",
    "tell me about", "explain", "please explain", "i want to know about",
)


def _to_search_query(question: str) -> str:
    """Turn a natural question into a keyword-style search query."""
    q = (question or "").strip().rstrip("?").strip().lower()
    changed = True
    while changed:  # strip stacked lead-ins, e.g. "please explain what is X"
        changed = False
        for lead in _LEAD_INS:
            if q.startswith(lead + " "):
                q = q[len(lead):].strip()
                changed = True
    return q or (question or "").strip()


def _host_allowed(url: str, domains: list[str]) -> bool:
    """Is this URL genuinely on a whitelisted domain (or a subdomain of one)?"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


def search_trusted(question: str) -> list[dict]:
    """Return [{title, url, content}] from whitelisted sources — or [] if we
    can't/shouldn't. Never raises: a failed fallback must degrade to a decline,
    not to an error."""
    if not settings.web_fallback_enabled or not settings.tavily_api_key:
        return []
    q = (question or "").strip()
    if not q:
        return []
    domains = trusted_domains()

    try:
        res = httpx.post(
            "https://api.tavily.com/search",
            # Tavily authenticates with a Bearer token header (the older
            # api_key-in-body form is rejected with a 401).
            headers={
                "Authorization": f"Bearer {settings.tavily_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": _to_search_query(q),
                "include_domains": domains,  # ← the whitelist, asked of the provider
                "max_results": settings.web_max_results,
                "search_depth": "basic",
            },
            timeout=20,
        )
        if res.status_code >= 300:
            print(f"[web_fallback] search failed: {res.status_code} {res.text[:160]}")
            return []
        data = res.json()
        out: list[dict] = []
        for r in data.get("results", []):
            content = (r.get("content") or "").strip()
            url = (r.get("url") or "").strip()
            if not content or not url:
                continue
            # DEFENCE IN DEPTH: re-check the domain ourselves. `include_domains`
            # has been observed returning off-whitelist results (youtube.com,
            # whatsapp.com) for awkward queries. For a health assistant, grounding
            # on an unvetted page is the one failure we must never allow — so the
            # provider's filter is a request, and THIS is the guarantee.
            if not _host_allowed(url, domains):
                print(f"[web_fallback] dropped off-whitelist result: {url[:80]}")
                continue
            out.append({
                "title": (r.get("title") or url).strip(),
                "url": url,
                "content": content,
            })
        return out
    except Exception as e:
        print(f"[web_fallback] non-fatal: {e}")
        return []
