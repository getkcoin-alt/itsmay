"""IM-1.3 — chat errors carry a calm, speakable message for the voice client."""

from __future__ import annotations

from apps.api.routers.chat import _classify_chat_error


class _FakeKeys:
    def __init__(self, statuses):
        self._statuses = statuses

    def status(self):
        return {"keys": self._statuses}


class _FakeLLM:
    def __init__(self, statuses):
        self.keys = _FakeKeys(statuses)


def test_rate_limit_all_keys_cooling_is_spoken():
    llm = _FakeLLM([{"active": False}, {"active": False}, {"active": False}])
    p = _classify_chat_error(
        RuntimeError("LLM provider unavailable after 5 attempt(s) (last HTTP 429)"), llm
    )
    assert p["kind"] == "rate_limit"
    assert "rate-limited" in p["speak"].lower()
    assert "3" in p["speak"]  # tells him all 3 keys are tapped


def test_rate_limit_single_key_is_spoken():
    llm = _FakeLLM([{"active": False}])
    p = _classify_chat_error(RuntimeError("HTTP 429 rate limit reached"), llm)
    assert p["kind"] == "rate_limit"
    assert p["speak"]


def test_rate_limit_some_active_says_rotating():
    llm = _FakeLLM([{"active": False}, {"active": True}])
    p = _classify_chat_error(RuntimeError("rate limit reached"), llm)
    assert p["kind"] == "rate_limit"
    assert "rotating" in p["speak"].lower()


def test_generic_error_is_speakable_not_raw():
    llm = _FakeLLM([{"active": True}])
    p = _classify_chat_error(RuntimeError("some weird boom"), llm)
    assert p["kind"] == "error"
    assert p["speak"]
    assert "boom" in p["error"]  # raw detail preserved for logs/CLI


def test_classifier_survives_broken_keypool():
    class Boom:
        def status(self):
            raise RuntimeError("no pool")

    llm = type("L", (), {"keys": Boom()})()
    p = _classify_chat_error(RuntimeError("429 rate limit"), llm)
    assert p["kind"] == "rate_limit"
    assert p["speak"]  # still speakable even if the pool read fails
