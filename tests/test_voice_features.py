"""Tests for the NumPy voiceprint embedder used by the web companion."""

from __future__ import annotations

import numpy as np

from core.companion.speaker_id import best_match
from core.companion.voice_features import VOICEPRINT_DIM, embed_pcm

SR = 16000


def _voiceish(f0: float, formants: list[float], *, seed: int, dur: float = 3.0) -> np.ndarray:
    """A crude glottal-source-plus-formants signal, distinct per (f0, formants)."""
    r = np.random.default_rng(seed)
    t = np.arange(int(SR * dur)) / SR
    sig = np.zeros_like(t)
    for h in range(1, 20):
        sig += (1.0 / h) * np.sin(2 * np.pi * f0 * h * t)
    for ff in formants:
        sig += 0.5 * np.sin(2 * np.pi * ff * t) * np.exp(-(t % 0.1) * 3)
    sig += 0.02 * r.standard_normal(t.size)
    return (sig / np.max(np.abs(sig)) * 0.9 * 32767).astype(np.int16)


def test_shape_and_determinism():
    a = _voiceish(120, [500, 1500, 2500], seed=1)
    v1 = embed_pcm(a)
    assert v1.shape == (VOICEPRINT_DIM,)
    assert v1.dtype == np.float32
    assert np.allclose(embed_pcm(a), v1)  # deterministic


def test_l2_normalized():
    v = embed_pcm(_voiceish(150, [700, 1800], seed=2))
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5


def test_too_short_is_zero_vector():
    v = embed_pcm(np.zeros(50, dtype=np.int16))
    assert v.shape == (VOICEPRINT_DIM,)
    assert not v.any()


def test_same_speaker_closer_than_different():
    from core.companion.speaker_id import _cosine

    a1 = embed_pcm(_voiceish(120, [500, 1500, 2500], seed=1))
    a2 = embed_pcm(_voiceish(120, [500, 1500, 2500], seed=2))  # same "voice"
    b1 = embed_pcm(_voiceish(210, [800, 2200, 3000], seed=3))  # different voice
    assert _cosine(a1, a2) >= _cosine(a1, b1)


def test_identify_routes_to_correct_enrolled_profile():
    a1 = embed_pcm(_voiceish(120, [500, 1500, 2500], seed=1))
    a2 = embed_pcm(_voiceish(120, [500, 1500, 2500], seed=2))
    b1 = embed_pcm(_voiceish(210, [800, 2200, 3000], seed=3))
    profiles = [("A", a1), ("B", b1)]
    assert best_match(a2, profiles).profile_id == "A"
    assert best_match(b1, profiles).profile_id == "B"


def test_int16_and_float_equivalent():
    pcm16 = _voiceish(130, [600, 1700], seed=4)
    pcm_f = pcm16.astype(np.float32) / 32768.0
    assert np.allclose(embed_pcm(pcm16), embed_pcm(pcm_f), atol=1e-5)
