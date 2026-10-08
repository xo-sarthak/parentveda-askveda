"""The model probe says plainly why a model does not answer (2026-09-27).

No network: the client is swapped for a stand-in that raises what the provider
would. The point is the words, because they are what an alert or a log shows.
"""

from types import SimpleNamespace

import pytest

from app import llm


@pytest.fixture(autouse=True)
def _groq_path(monkeypatch):
    # These pin the OpenAI-compatible (Groq) path. Without this they would
    # follow whatever LLM_PROVIDER the local .env selects (2026-10-08).
    monkeypatch.setattr(llm.settings, "llm_provider", "groq")


class _Raises:
    def __init__(self, exc):
        self._exc = exc
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_):
        raise self._exc


class NotFoundError(Exception):
    pass


class AuthenticationError(Exception):
    pass


def _answers(text):
    msg = SimpleNamespace(content=text)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    return SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=lambda **_: resp)))


def test_a_retired_model_is_named_as_retired(monkeypatch):
    monkeypatch.setattr(llm, "_client", _Raises(NotFoundError(
        "Error code: 404 - model_not_found: The model has been decommissioned")))
    r = llm.probe()
    assert r["ok"] is False
    assert "retired" in r["reason"]
    assert "LLM_MODEL" in r["reason"]


def test_a_bad_key_is_named_as_a_bad_key(monkeypatch):
    monkeypatch.setattr(llm, "_client", _Raises(AuthenticationError("Invalid API Key")))
    r = llm.probe()
    assert r["ok"] is False
    assert "LLM_API_KEY" in r["reason"]


def test_a_working_model_answers(monkeypatch):
    monkeypatch.setattr(llm, "_client", _answers("OK"))
    r = llm.probe()
    assert r == {"ok": True, "model": llm.settings.llm_model,
                 "reason": "answered", "detail": "OK"}


def test_the_probe_never_raises(monkeypatch):
    monkeypatch.setattr(llm, "_client", _Raises(RuntimeError("anything at all")))
    r = llm.probe()
    assert r["ok"] is False
    assert r["reason"].startswith("unexpected error")
