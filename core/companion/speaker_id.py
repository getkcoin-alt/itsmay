"""Voiceprint speaker-ID — "who is talking?".

Two cleanly separated halves:

- **Matching** (`best_match`) is pure math over speaker embeddings — no models,
  no I/O — so it's fully unit-testable. Given a sample voiceprint and the enrolled
  profiles, it returns the best cosine match above a threshold, else "new person".
- **Embedding** (`VoiceEncoder`) wraps Resemblyzer's torch speaker encoder, lazily
  loaded (Mac/edge only), mirroring the embedder/STT lazy pattern. Injectable so
  tests never touch torch.

The runtime embeds an utterance, then `identify()`s it against `ProfileStore.all()`
to pick the profile (or decide it's someone new → enroll).
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

from core.logging import get_logger

log = get_logger(__name__)

# Default cosine threshold for "same speaker". Resemblyzer d-vectors put
# same-speaker pairs well above this and different-speaker pairs below; tunable
# via SPEAKER_MATCH_THRESHOLD.
DEFAULT_THRESHOLD = 0.75


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


@dataclass(slots=True)
class Match:
    profile_id: str | None  # None → no enrolled voice matched (a new person)
    score: float  # best cosine similarity seen (0..1)


def best_match(
    sample: np.ndarray,
    profiles: list[tuple[str, np.ndarray | None]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> Match:
    """Nearest enrolled voice by cosine similarity.

    `profiles` is a list of (profile_id, voiceprint). Entries with no voiceprint
    are skipped. Returns the best match if its score clears `threshold`, else a
    Match with `profile_id=None` (treat as a new/unknown person).
    """
    sample = np.asarray(sample, dtype=np.float32)
    best_id: str | None = None
    best_score = -1.0
    for pid, vp in profiles:
        if vp is None:
            continue
        s = _cosine(sample, np.asarray(vp, dtype=np.float32))
        if s > best_score:
            best_id, best_score = pid, s
    if best_id is not None and best_score >= threshold:
        return Match(best_id, best_score)
    return Match(None, max(best_score, 0.0))


class VoiceEncoder:
    """Lazy Resemblyzer wrapper → a fixed-length speaker embedding (d-vector).

    Heavy (pulls torch); not imported or loaded until first use. Mac/edge only —
    in tests, inject a fake encoder instead.
    """

    def __init__(self) -> None:
        self._model: Any | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> Any:
        with self._lock:
            if self._model is None:
                try:
                    from resemblyzer import VoiceEncoder as _RVE
                except ImportError as e:
                    raise RuntimeError(
                        "resemblyzer not installed — install the companion extra: "
                        'pip install -e ".[mini]"'
                    ) from e
                log.info("companion.voice_encoder.loading")
                self._model = _RVE()
                log.info("companion.voice_encoder.loaded")
            return self._model

    async def embed_wav(self, wav: np.ndarray, sample_rate: int) -> np.ndarray:
        return await asyncio.to_thread(self._embed_sync, wav, sample_rate)

    def _embed_sync(self, wav: np.ndarray, sample_rate: int) -> np.ndarray:
        from resemblyzer import preprocess_wav

        model = self._ensure()
        processed = preprocess_wav(np.asarray(wav, dtype=np.float32), source_sr=sample_rate)
        return np.asarray(model.embed_utterance(processed), dtype=np.float32)


class SpeakerIdentifier:
    """Embeds utterances and identifies them against enrolled profiles."""

    def __init__(self, *, threshold: float | None = None, encoder: VoiceEncoder | None = None):
        if threshold is None:
            try:
                from core.config import get_settings

                threshold = get_settings().speaker_match_threshold
            except Exception:
                threshold = DEFAULT_THRESHOLD
        self.threshold = threshold
        self._encoder = encoder or VoiceEncoder()

    async def embed(self, wav: np.ndarray, sample_rate: int) -> np.ndarray:
        return await self._encoder.embed_wav(wav, sample_rate)

    def identify(
        self, sample: np.ndarray, profiles: list[tuple[str, np.ndarray | None]]
    ) -> Match:
        return best_match(sample, profiles, threshold=self.threshold)
