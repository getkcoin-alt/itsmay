"""Lightweight browser-PCM voice features for Mini's web enrollment path.

This is deliberately separate from the optional Resemblyzer encoder used by the
native companion.  It uses only NumPy, returns a fixed 48-dimensional normalized
vector, and never stores raw audio.  The router already compares voiceprints
only when their shapes match, so these vectors cannot accidentally be compared
with Resemblyzer d-vectors.

The feature is a compact log-mel spectral summary (mean + standard deviation).
It is useful for a personal-device convenience identity signal, not a claim of
forensic-grade biometric authentication.
"""

from __future__ import annotations

import numpy as np

TARGET_SAMPLE_RATE = 16_000
MEL_BANDS = 24
EMBED_DIM = MEL_BANDS * 2
_MIN_AUDIO_SECONDS = 0.35
_MIN_RMS = 0.004
_FRAME_SECONDS = 0.025
_HOP_SECONDS = 0.010
_EPS = 1e-8


def _zero() -> np.ndarray:
    return np.zeros(EMBED_DIM, dtype=np.float32)


def _hz_to_mel(hz: np.ndarray | float) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + np.asarray(hz, dtype=np.float64) / 700.0)


def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _resample_linear(wav: np.ndarray, source_rate: int) -> np.ndarray:
    if source_rate == TARGET_SAMPLE_RATE:
        return wav.astype(np.float32, copy=False)
    duration = len(wav) / float(source_rate)
    target_length = max(1, int(round(duration * TARGET_SAMPLE_RATE)))
    old_x = np.linspace(0.0, duration, num=len(wav), endpoint=False, dtype=np.float64)
    new_x = np.linspace(0.0, duration, num=target_length, endpoint=False, dtype=np.float64)
    return np.interp(new_x, old_x, wav).astype(np.float32)


def _mel_filterbank(n_fft: int) -> np.ndarray:
    bins = n_fft // 2 + 1
    low_mel = float(_hz_to_mel(80.0))
    high_mel = float(_hz_to_mel(TARGET_SAMPLE_RATE / 2.0))
    points_hz = _mel_to_hz(np.linspace(low_mel, high_mel, MEL_BANDS + 2))
    fft_hz = np.linspace(0.0, TARGET_SAMPLE_RATE / 2.0, bins)
    bank = np.zeros((MEL_BANDS, bins), dtype=np.float32)
    for i in range(MEL_BANDS):
        left, center, right = points_hz[i : i + 3]
        rising = (fft_hz - left) / max(center - left, _EPS)
        falling = (right - fft_hz) / max(right - center, _EPS)
        bank[i] = np.maximum(0.0, np.minimum(rising, falling))
    return bank


def embed_pcm(pcm: np.ndarray, sample_rate: int) -> np.ndarray:
    """Return a deterministic fixed-length voice feature vector.

    Invalid rates, too-short clips, silence, non-finite input, or feature
    degeneracy return the all-zero vector.  The caller uses ``np.any`` to reject
    those samples, which keeps malformed/insufficient biometric input fail-closed.
    """

    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool):
        return _zero()
    if not 8_000 <= sample_rate <= 48_000:
        return _zero()

    raw = np.asarray(pcm)
    if raw.ndim != 1 or raw.size < int(sample_rate * _MIN_AUDIO_SECONDS):
        return _zero()
    if not np.issubdtype(raw.dtype, np.number):
        return _zero()

    wav = raw.astype(np.float32, copy=False)
    if not np.isfinite(wav).all():
        return _zero()
    # Browser endpoint supplies signed int16 PCM.  Also accept already-normalized
    # floats in tests/adapters without amplifying them again.
    peak = float(np.max(np.abs(wav))) if wav.size else 0.0
    if peak > 1.5:
        wav = wav / 32768.0
    wav = np.clip(wav, -1.0, 1.0)
    wav = wav - float(np.mean(wav))
    rms = float(np.sqrt(np.mean(wav * wav)))
    if not np.isfinite(rms) or rms < _MIN_RMS:
        return _zero()

    wav = _resample_linear(wav, sample_rate)
    if len(wav) < int(TARGET_SAMPLE_RATE * _MIN_AUDIO_SECONDS):
        return _zero()

    # Pre-emphasis reduces microphone/room low-frequency dominance.
    emphasized = np.empty_like(wav)
    emphasized[0] = wav[0]
    emphasized[1:] = wav[1:] - 0.97 * wav[:-1]

    frame_len = int(round(TARGET_SAMPLE_RATE * _FRAME_SECONDS))
    hop = int(round(TARGET_SAMPLE_RATE * _HOP_SECONDS))
    if len(emphasized) < frame_len:
        return _zero()
    frame_count = 1 + (len(emphasized) - frame_len) // hop
    if frame_count < 3:
        return _zero()

    starts = np.arange(frame_count)[:, None] * hop
    offsets = np.arange(frame_len)[None, :]
    frames = emphasized[starts + offsets]
    frames = frames * np.hanning(frame_len).astype(np.float32)

    n_fft = 512
    spectrum = np.abs(np.fft.rfft(frames, n=n_fft, axis=1)) ** 2
    energies = spectrum @ _mel_filterbank(n_fft).T
    log_mel = np.log(np.maximum(energies, _EPS))

    # Cepstral mean-style centering across bands makes the summary less sensitive
    # to absolute gain while preserving the speaker's spectral shape.
    log_mel = log_mel - np.mean(log_mel, axis=1, keepdims=True)
    feature = np.concatenate(
        [np.mean(log_mel, axis=0), np.std(log_mel, axis=0)],
        axis=0,
    ).astype(np.float32)
    if feature.shape != (EMBED_DIM,) or not np.isfinite(feature).all():
        return _zero()
    norm = float(np.linalg.norm(feature))
    if norm <= _EPS:
        return _zero()
    return feature / norm


__all__ = ["EMBED_DIM", "TARGET_SAMPLE_RATE", "embed_pcm"]
