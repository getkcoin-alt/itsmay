"""Read-only event sources for Continuous Presence.

A source observes real system state and emits normalized PresenceEvents. Sources
never execute tools or mutate external systems.
"""

from __future__ import annotations

import asyncio

from core.logging import get_logger
from core.presence.models import PresenceEvent
from core.presence.runtime import PresenceRuntime
from core.worker.bridge import get_worker_bridge

log = get_logger(__name__)


async def watch_worker_presence(
    runtime: PresenceRuntime,
    *,
    interval: float = 2.0,
) -> None:
    """Emit a real event when the Mac worker connect state changes.

    The initial observation establishes baseline and is intentionally silent.
    A later offline→online transition is attention-worthy and carries explicit
    speakable text. A disconnect is recorded but not queued for speech because
    the Mac output client is likely unavailable at that moment.
    """

    if interval <= 0:
        raise ValueError("interval must be > 0")

    bridge = get_worker_bridge()
    previous = bridge.worker_online()

    try:
        while True:
            await asyncio.sleep(interval)
            current = bridge.worker_online()
            if current == previous:
                continue

            if current:
                event = PresenceEvent(
                    type="worker.connected",
                    source="worker_bridge",
                    domain="system",
                    importance=0.95,
                    urgency=0.65,
                    confidence=1.0,
                    novelty=1.0,
                    goal_relevance=0.95,
                    interruption_cost=0.1,
                    payload={
                        "message": (
                            "BOYI, Mac worker connected. The local execution path "
                            "is available again."
                        )
                    },
                )
            else:
                event = PresenceEvent(
                    type="worker.disconnected",
                    source="worker_bridge",
                    domain="system",
                    importance=0.7,
                    urgency=0.4,
                    confidence=1.0,
                    novelty=1.0,
                    goal_relevance=0.8,
                    interruption_cost=0.75,
                    payload={},
                )

            accepted = await runtime.submit(event)
            log.info(
                "presence.source.worker_state",
                online=current,
                event_id=event.id,
                accepted=accepted,
            )
            previous = current
    except asyncio.CancelledError:
        raise
