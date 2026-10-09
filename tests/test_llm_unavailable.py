"""When the model provider fails (2026-10-09): a calm answer, not a 500.

No credit, an outage, a revoked key: before, the exception reached FastAPI as an
HTTP 500, which the app reads as "no internet". What is pinned here: she gets a
readable message that names neither the provider nor billing, the reads we
already found still come with it, and nothing is cached (a cached break would
outlive the outage).
"""

from app import answer


def _boom(_messages):
    raise RuntimeError("Your credit balance is too low to access the Anthropic API.")


_HIT = {"similarity": 0.9, "content": "Folic acid helps.", "kind": "content"}
_SECS = {"content": [{"title": "Folic acid, early"}], "videos": [],
         "products": [], "services": []}


def _wire(monkeypatch, stored):
    monkeypatch.setattr(answer, "complete", _boom)
    monkeypatch.setattr(answer.guardrails, "within_rate_limit", lambda k: True)
    monkeypatch.setattr(answer.guardrails, "within_spend_cap", lambda: True)
    monkeypatch.setattr(answer.guardrails, "red_flag_response", lambda q: None)
    monkeypatch.setattr(answer, "embed_query", lambda q: [0.0] * 384)
    monkeypatch.setattr(answer.cache, "lookup", lambda *a: None)
    monkeypatch.setattr(answer.cache, "store", lambda *a: stored.append(a))
    monkeypatch.setattr(answer, "retrieve", lambda *a, **k: [_HIT])
    monkeypatch.setattr(answer.sections, "enrich_doc_ids", lambda r: None)
    monkeypatch.setattr(answer.sections, "build_sections", lambda r, lang=None: _SECS)
    monkeypatch.setattr(answer.sections, "attach_bodies", lambda c: None)
    monkeypatch.setattr(answer.usage, "log_usage", lambda **k: None)


def test_a_provider_failure_is_a_calm_answer_with_our_reads(monkeypatch):
    stored = []
    _wire(monkeypatch, stored)
    out = answer.answer("Should I take folic acid?", user_key="install:t")
    assert out["source"] == "llm_unavailable"
    assert out["answer"] == answer._MSG_RESTING
    assert out["content"] == _SECS["content"]
    assert stored == []


def test_the_message_names_no_provider_and_no_billing():
    m = answer._MSG_RESTING.lower()
    for word in ("anthropic", "claude", "groq", "credit", "balance", "billing", "error"):
        assert word not in m


def test_the_web_fallback_failing_lands_the_same_way(monkeypatch):
    monkeypatch.setattr(answer, "complete", _boom)
    monkeypatch.setattr(answer.web_fallback, "search_trusted",
                        lambda q: [{"url": "https://www.nhs.uk/x", "content": "x"}])
    out, billed = answer._try_trusted_web(
        "q", user_key="k", channel="app", stage_key="", stage_note="",
        q_vector=[], personal=False)
    assert out["source"] == "llm_unavailable"
    assert billed is False
