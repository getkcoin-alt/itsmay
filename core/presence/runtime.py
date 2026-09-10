"""Always-running presence substrate for Scrappy.

The runtime receives real normalized events, maintains a heartbeat, applies a
deterministic initiative pre-filter and records whether an event deserves
attention. A SPEAK decision may place an explicitly supplied human-readable
message into an in-memory proactive outbox. It never invents work, sends email,
runs tools, trades, deploys, or mutates an external system itself.

Consequential capabilities remain behind the existing policy/approval layers.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections import deque
from pathlib import Path

from core.logging import get_logger
from core.presence.models import (
    DecisionKind,
    PresenceDecision,
    PresenceEvent,
    PresenceUtterance,
)

log = get_logger(__name__)

_DEFAULT_HEARTBEAT_SECONDS = 5.0
_DEFAULT_SPEAK_THRESHOLD = 0.72
_MAX_RECENT = 100
_MAX_SEEN_IDS = 4096
_MAX_OUTBOX = 100


def _resolve_node_id(state_dir: Path | None = None) -> tuple[str, bool]:
    """Return a real stable node id when possible.

    SCRAPPY_NODE_ID is authoritative in deployed environments. Otherwise a UUID
    is persisted in a small local state file. If persistence is unavailable we
    still return a real process identity, but mark it non-persistent in status.
    """

    configured = os.getenv("SCRAPPY_NODE_ID", "").strip()
    if configured:
        return configured, True

    root = state_dir or Path(
        os.getenv("SCRAPPY_STATE_DIR", "~/.local/state/vault-zeta")
    ).expanduser()
    path = root / "presence-node-id"
    try:
        root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value, True
        value = str(uuid.uuid4())
        path.write_text(value + "\n", encoding="utf-8")
        return value, True
    except OSError:
        return str(uuid.uuid4()), False


def _event_message(event: PresenceEvent) -> str | None:
    """Return explicit speakable text carried by an event, never invented text."""

    value = event.payload.get("message")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:4000] if value else None


class PresenceRuntime:
    """Continuous event/heartbeat loop with deterministic attention decisions."""

    def __init__(
        self,
        *,
        heartbeat_interval: float = _DEFAULT_HEARTBEAT_SECONDS,
        speak_threshold: float = _DEFAULT_SPEAK_THRESHOLD,
        state_dir: Path | None = None,
    ) -> None:
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be > 0")
        if not 0.0 <= speak_threshold <= 1.0:
            raise ValueError("speak_threshold must be between 0 and 1")

        self.heartbeat_interval = heartbeat_interval
        self.speak_threshold = speak_threshold
        self.node_id, self.node_identity_persistent = _resolve_node_id(state_dir)
        self._queue: asyncio.Queue[PresenceEvent] = asyncio.Queue(maxsize=1000)
        self._outbox: asyncio.Queue[PresenceUtterance] = asyncio.Queue(maxsize=_MAX_OUTBOX)
        self._recent: deque[PresenceDecision] = deque(maxlen=_MAX_RECENT)
        self._recent_events: deque[PresenceEvent] = deque(maxlen=_MAX_RECENT)
        self._seen_ids: set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=_MAX_SEEN_IDS)
        self._runner: asyncio.Task | None = None
        self._heartbeat_runner: asyncio.Task | None = None
        self._started_monotonic: float | None = None
        self._last_heartbeat_monotonic: float | None = None
        self._last_heartbeat_wall: float | None = None
        self._last_event: PresenceEvent | None = None
        self._last_decision: PresenceDecision | None = None
        self._last_utterance: PresenceUtterance | None = None

    @property
    def running(self) -> bool:
        return bool(self._runner and not self._runner.done())

    async def start(self) -> None:
        if self.running:
            return
        self._started_monotonic = time.monotonic()
        self._touch_heartbeat()
        self._runner = asyncio.create_task(self._run(), name="scrappy-presence-loop")
        self._heartbeat_runner = asyncio.create_task(
            self._heartbeat_loop(), name="scrappy-presence-heartbeat"
        )
        log.info(
            "presence.started",
            node_id=self.node_id,
            node_identity_persistent=self.node_identity_persistent,
        )

    async def stop(self) -> None:
        tasks = [task for task in (self._runner, self._heartbeat_runner) if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._runner = None
        self._heartbeat_runner = None
        log.info("presence.stopped", node_id=self.node_id)

    async def submit(self, event: PresenceEvent) -> bool:
        """Accept an event once. False means the id was already seen."""

        if event.id in self._seen_ids:
            log.info("presence.duplicate", event_id=event.id, trace_id=event.trace_id)
            return False

        if len(self._seen_order) == self._seen_order.maxlen:
            oldest = self._seen_order.popleft()
            self._seen_ids.discard(oldest)
        self._seen_order.append(event.id)
        self._seen_ids.add(event.id)
        await self._queue.put(event)
        return True

    def evaluate(self, event: PresenceEvent) -> PresenceDecision:
        """Cheap deterministic pre-filter; no LLM call and no external side effects."""

        raw = (
            0.25 * event.importance
            + 0.25 * event.urgency
            + 0.20 * event.goal_relevance
            + 0.15 * event.novelty
            + 0.15 * event.confidence
            - 0.25 * event.interruption_cost
        )
        score = max(0.0, min(1.0, raw))
        decision = (
            DecisionKind.SPEAK if score >= self.speak_threshold else DecisionKind.SILENCE
        )
        reason = (
            f"initiative score {score:.3f} {'met' if decision == DecisionKind.SPEAK else 'below'} "
            f"threshold {self.speak_threshold:.3f}"
        )
        return PresenceDecision(
            event_id=event.id,
            trace_id=event.trace_id,
            decision=decision,
            score=score,
            reason=reason,
        )

    async def next_utterance(self, wait: float = 25.0) -> PresenceUtterance | None:
        """Long-poll the proactive outbox.

        Delivery is intentionally at-most-once in this V1 slice. Durable/acked
        delivery belongs in the Vault continuity layer, not hidden in this queue.
        """

        try:
            item = await asyncio.wait_for(self._outbox.get(), timeout=wait)
        except TimeoutError:
            return None
        self._outbox.task_done()
        return item

    async def _run(self) -> None:
        try:
            while True:
                event = await self._queue.get()
                try:
                    self._last_event = event
                    self._recent_events.append(event)
                    decision = self.evaluate(event)
                    self._last_decision = decision
                    self._recent.append(decision)
                    log.info(
                        "presence.decision",
                        event_id=event.id,
                        trace_id=event.trace_id,
                        event_type=event.type,
                        source=event.source,
                        domain=event.domain,
                        decision=decision.decision.value,
                        score=decision.score,
                    )

                    # SPEAK means "worth interrupting". The runtime still refuses
                    # to fabricate words: only an explicit payload.message becomes
                    # an output item. Later a reasoning stage may safely generate
                    # that message before this point.
                    if decision.decision == DecisionKind.SPEAK:
                        text = _event_message(event)
                        if text:
                            utterance = PresenceUtterance(
                                event_id=event.id,
                                trace_id=event.trace_id,
                                text=text,
                                source=event.source,
                                domain=event.domain,
                            )
                            try:
                                self._outbox.put_nowait(utterance)
                                self._last_utterance = utterance
                                log.info(
                                    "presence.utterance_queued",
                                    utterance_id=utterance.id,
                                    event_id=event.id,
                                    trace_id=event.trace_id,
                                )
                            except asyncio.QueueFull:
                                log.warning(
                                    "presence.outbox_full",
                                    event_id=event.id,
                                    trace_id=event.trace_id,
                                )
                except Exception as exc:  # never let one malformed handler kill the breath
                    log.exception(
                        "presence.event_failed",
                        event_id=event.id,
                        trace_id=event.trace_id,
                        error=str(exc),
                    )
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            raise

    def _touch_heartbeat(self) -> None:
        self._last_heartbeat_monotonic = time.monotonic()
        self._last_heartbeat_wall = time.time()

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                self._touch_heartbeat()
        except asyncio.CancelledError:
            raise

    def recent(self) -> list[dict]:
        return [item.model_dump(mode="json") for item in reversed(self._recent)]

    def recent_events(self) -> list[dict]:
        return [item.model_dump(mode="json") for item in reversed(self._recent_events)]

    def status(self) -> dict:
        now = time.monotonic()
        uptime = (
            max(0.0, now - self._started_monotonic)
            if self._started_monotonic is not None
            else None
        )
        heartbeat_age = (
            max(0.0, now - self._last_heartbeat_monotonic)
            if self._last_heartbeat_monotonic is not None
            else None
        )
        return {
            "identity": "scrappy",
            "runtime": "online" if self.running else "offline",
            "node_id": self.node_id,
            "node_identity_persistent": self.node_identity_persistent,
            "uptime_seconds": round(uptime, 3) if uptime is not None else None,
            "heartbeat_interval_seconds": self.heartbeat_interval,
            "last_heartbeat_unix": self._last_heartbeat_wall,
            "heartbeat_age_seconds": (
                round(heartbeat_age, 3) if heartbeat_age is not None else None
            ),
            "queue_depth": self._queue.qsize(),
            "outbox_depth": self._outbox.qsize(),
            "last_event_id": self._last_event.id if self._last_event else None,
            "last_event_type": self._last_event.type if self._last_event else None,
            "last_decision": (
                self._last_decision.model_dump(mode="json") if self._last_decision else None
            ),
            "last_utterance": (
                self._last_utterance.model_dump(mode="json") if self._last_utterance else None
            ),
            "decision_engine": "deterministic_prefilter_v1",
            "side_effects": "proactive_text_outbox_only",
            "outbox_delivery": "at_most_once",
            "persistence": "node_identity_only",
        }


_runtime: PresenceRuntime | None = None


def get_presence_runtime() -> PresenceRuntime:
    global _runtime
    if _runtime is None:
        _runtime = PresenceRuntime()
    return _runtime
