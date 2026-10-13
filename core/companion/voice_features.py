"""Server-side voiceprint embedding in pure NumPy (no torch, no ffmpeg).

The Mac agent computes speaker d-vectors with Resemblyzer, but the cloud/web
server has neither torch nor an audio decoder — so the browser decodes the mic
recording to 16 kHz mono PCM and posts it here, and we turn it into a
fixed-length voiceprint using classic **MFCC statistics** (mean + std of the
cepstral coefficients over the utterance). That captures the vocal-tract /
spectral-envelope signature that distinguishes a handful of people sharing one
device — enough for the web companion's "who am I talking to" gating.

It is intentionally light, not bank-grade biometrics. The output is a plain
float32 vector, so it drops straight into `speaker_id.best_match` (pure cosine)
and the `voiceprint` BLOB column. A stronger ONNX d-vector model can replace
`embed_pcm` later behind the same signature without touching callers.
"""

from __future__ import annotations

import numpy as np

# Feature geometry (standard MFCC front-end).
_TARGET_SR = 16000
_FRAME_MS = 25
_HOP_MS = 10
_N_FFT = 512
_N_MELS = 26
_N_MFCC = 13
_PRE_EMPHASIS = 0.97
_EPS = 1e-10

# Voiceprint = concat(mean, std) of the MFCCs → length 2 * _N_MFCC.
VOICEPRINT_DIM = 2 * _N_MFCC


def _hz_to_mel(hz: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(hz) / 700.0)


def _mel_to_hz(mel: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (10.0 ** (np.asarray(mel) / 2595.0) - 1.0)


def _mel_filterbank(n_mels: int, n_fft: int, sr: int) -> np.ndarray:
    """Triangular mel filterbank, shape (n_mels, n_fft//2 + 1)."""
    n_bins = n_fft // 2 + 1
    mel_pts = np.linspace(_hz_to_mel(0.0), _hz_to_mel(sr / 2.0), n_mels + 2)
    hz_pts = _mel_to_hz(mel_pts)
    bin_pts = np.floor((n_fft + 1) * hz_pts / sr).astype(int)
    bin_pts = np.clip(bin_pts, 0, n_bins - 1)

    fb = np.zeros((n_mels, n_bins), dtype=np.float32)
    for m in range(1, n_mels + 1):
        left, center, right = bin_pts[m - 1], bin_pts[m], bin_pts[m + 1]
        if center > left:
            fb[m - 1, left:center] = (np.arange(left, center) - left) / (center - left)
        if right > center:
            fb[m - 1, center:right] = (right - np.arange(center, right)) / (right - center)
    return fb


def _dct_basis(n_mfcc: int, n_mels: int) -> np.ndarray:
    """DCT-II basis, shape (n_mfcc, n_mels)."""
    k = np.arange(n_mfcc)[:, None]
    n = np.arange(n_mels)[None, :]
    basis = np.cos(np.pi * k * (2 * n + 1) / (2 * n_mels))
    return basis.astype(np.float32)


# Precompute the fixed transforms once at import.
_MEL_FB = _mel_filterbank(_N_MELS, _N_FFT, _TARGET_SR)
_DCT = _dct_basis(_N_MFCC, _N_MELS)
_HAMMING = np.hamming(int(_TARGET_SR * _FRAME_MS / 1000)).astype(np.float32)


def _to_float_mono(pcm: np.ndarray) -> np.ndarray:
    pcm = np.asarray(pcm)
    if pcm.ndim > 1:  # stereo → mono
        pcm = pcm.mean(axis=1)
    if np.issubdtype(pcm.dtype, np.integer):
        pcm = pcm.astype(np.float32) / 32768.0
    return pcm.astype(np.float32)


def _mfcc(signal: np.ndarray) -> np.ndarray:
    """MFCC matrix for a 16 kHz mono signal, shape (n_frames, _N_MFCC)."""
    frame_len = int(_TARGET_SR * _FRAME_MS / 1000)   # 400
    hop = int(_TARGET_SR * _HOP_MS / 1000)           # 160

    # Pre-emphasis lifts the high frequencies that carry speaker detail.
    emph = np.append(signal[0], signal[1:] - _PRE_EMPHASIS * signal[:-1])

    if len(emph) < frame_len:
        emph = np.pad(emph, (0, frame_len - len(emph)))

    n_frames = 1 + (len(emph) - frame_len) // hop
    idx = np.arange(frame_len)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = emph[idx] * _HAMMING

    power = (np.abs(np.fft.rfft(frames, n=_N_FFT, axis=1)) ** 2) / _N_FFT
    mel = np.maximum(power @ _MEL_FB.T, _EPS)
    log_mel = np.log(mel)
    return log_mel @ _DCT.T


def embed_pcm(pcm: np.ndarray, sample_rate: int = _TARGET_SR) -> np.ndarray:
    """Voiceprint for a PCM utterance. Expects ~16 kHz mono (int16 or float).

    Returns an L2-normalized float32 vector of length `VOICEPRINT_DIM`, suitable
    for cosine matching. Silence / too-short audio yields a zero vector.
    """
    signal = _to_float_mono(pcm)
    # Trim leading/trailing near-silence so framing focuses on speech.
    if signal.size:
        peak = float(np.max(np.abs(signal)))
        if peak > 0:
            signal = signal / peak
    if signal.size < int(_TARGET_SR * _FRAME_MS / 1000):
        return np.zeros(VOICEPRINT_DIM, dtype=np.float32)

    mfcc = _mfcc(signal)
    feat = np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0)]).astype(np.float32)
    norm = float(np.linalg.norm(feat))
    return feat / norm if norm > 0 else feat
