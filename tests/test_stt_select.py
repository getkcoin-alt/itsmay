"""Local STT must use a faster-whisper size name (whisper_model), not the cloud
model id (stt_model) — regression for the Mini AI `mini run` crash where
faster-whisper rejected 'whisper-large-v3-turbo'.
"""

from __future__ import annotations

from core.config import get_settings
from core.voice.stt_whisper import WhisperSTT


def test_local_provider_uses_whisper_model():
    s = get_settings()
    assert WhisperSTT(provider="local").model_size == s.whisper_model  # e.g. small.en
    assert "whisper-" not in WhisperSTT(provider="local").model_size  # not the cloud id


def test_groq_provider_uses_cloud_model():
    assert WhisperSTT(provider="groq").model_size == get_settings().stt_model


def test_explicit_model_overrides_provider_default():
    assert WhisperSTT(provider="local", model="tiny.en").model_size == "tiny.en"
