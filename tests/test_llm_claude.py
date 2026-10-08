"""The Claude path (2026-10-08): LLM_PROVIDER=anthropic.

No network: the Claude client is swapped for a stand-in. What is pinned here is
the edge translation and the two ways a Claude reply can go wrong quietly: a
refusal read as an empty answer (shown to her and cached), and the spend cap
measuring Claude's spend at Groq's prices.
"""

from types import SimpleNamespace

from app import answer, llm
from app.prompt import NO_ANSWER


class _Claude:
    """Records the request and returns a canned reply."""

    def __init__(self, reply):
        self.reply = reply
        self.sent = None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.sent = kwargs
        return self.reply


def _reply(text="Answer.", stop_reason="end_turn"):
    content = [SimpleNamespace(type="thinking", thinking=""),
               SimpleNamespace(type="text", text=text)] if text else []
    return SimpleNamespace(content=content, stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def _use_claude(monkeypatch, reply):
    fake = _Claude(reply)
    monkeypatch.setattr(llm.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(llm, "_claude", fake)
    return fake


_MSGS = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "Q"}]


def test_the_system_prompt_moves_to_its_own_field(monkeypatch):
    fake = _use_claude(monkeypatch, _reply())
    llm.complete(_MSGS)
    assert fake.sent["system"] == "SYS"
    assert fake.sent["messages"] == [{"role": "user", "content": "Q"}]
    assert fake.sent["model"] == llm.settings.anthropic_model
    # Claude 5.5 models reject temperature; effort is the dial.
    assert "temperature" not in fake.sent
    assert fake.sent["output_config"] == {"effort": llm.settings.anthropic_effort}
    # Haiku 5.5 has no server-side fallback, so none is asked for.
    assert "extra_body" not in fake.sent


def test_the_fallback_is_asked_for_on_models_that_have_one(monkeypatch):
    fake = _use_claude(monkeypatch, _reply())
    monkeypatch.setattr(llm.settings, "anthropic_model", "claude-opus-5-5")
    llm.complete(_MSGS)
    assert fake.sent["extra_body"] == {"fallbacks": "default"}


def test_only_text_blocks_become_the_answer(monkeypatch):
    _use_claude(monkeypatch, _reply("Grounded answer."))
    r = llm.complete(_MSGS)
    assert r == {"text": "Grounded answer.", "input_tokens": 1000, "output_tokens": 200}


def test_a_refusal_reads_as_no_answer_not_an_empty_answer(monkeypatch):
    _use_claude(monkeypatch, _reply(text="", stop_reason="refusal"))
    assert llm.complete(_MSGS)["text"] == NO_ANSWER


def test_an_empty_reply_reads_as_no_answer(monkeypatch):
    _use_claude(monkeypatch, _reply(text=""))
    assert llm.complete(_MSGS)["text"] == NO_ANSWER


def test_the_spend_cap_prices_claude_as_claude(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", "anthropic")
    # 1M in + 1M out at Haiku 5.5 = $0.10 + $0.50.
    assert answer._cost_usd(1_000_000, 1_000_000) == 0.6
    monkeypatch.setattr(llm.settings, "llm_provider", "groq")
    assert answer._cost_usd(1_000_000, 1_000_000) == 0.375


def test_an_empty_balance_is_named_as_no_credits(monkeypatch):
    class BadRequestError(Exception):
        pass

    def _raise(**_):
        raise BadRequestError("Your credit balance is too low to access the Anthropic API.")

    monkeypatch.setattr(llm.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(llm, "_claude", SimpleNamespace(
        messages=SimpleNamespace(create=_raise)))
    assert llm.probe()["reason"].startswith("no credits")


def test_the_probe_names_the_claude_key_when_claude_is_active(monkeypatch):
    class AuthenticationError(Exception):
        pass

    def _raise(**_):
        raise AuthenticationError("invalid x-api-key")

    monkeypatch.setattr(llm.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(llm, "_claude", SimpleNamespace(
        messages=SimpleNamespace(create=_raise)))
    r = llm.probe()
    assert r["ok"] is False
    assert "ANTHROPIC_API_KEY" in r["reason"]
    assert r["model"] == llm.settings.anthropic_model
