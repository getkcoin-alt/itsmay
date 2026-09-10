from __future__ import annotations

import asyncio

from core.presence.models import DecisionKind, PresenceEvent
from core.presence.runtime import PresenceRuntime


def test_low_value_event_stays_silent(tmp_path):
    runtime = PresenceRuntime(state_dir=tmp_path)
    decision = runtime.evaluate(
        PresenceEvent(
            type="timer",
            source="test",
            importance=0.1,
            urgency=0.0,
            confidence=1.0,
            novelty=0.1,
            goal_relevance=0.0,
            interruption_cost=0.8,
        )
    )
    assert decision.decision == DecisionKind.SILENCE


def test_high_value_event_crosses_speak_threshold(tmp_path):
    runtime = PresenceRuntime(state_dir=tmp_path)
    decision = runtime.evaluate(
        PresenceEvent(
            type="system_health",
            source="test",
            domain="system",
            importance=1.0,
            urgency=1.0,
            confidence=1.0,
            novelty=1.0,
            goal_relevance=1.0,
            interruption_cost=0.0,
        )
    )
    assert decision.decision == DecisionKind.SPEAK
    assert decision.score >= runtime.speak_threshold


def test_node_id_persists_across_runtime_instances(tmp_path):
    first = PresenceRuntime(state_dir=tmp_path)
    second = PresenceRuntime(state_dir=tmp_path)
    assert first.node_id == second.node_id
    assert first.node_identity_persistent is True
    assert second.node_identity_persistent is True


async def test_runtime_heartbeat_event_and_duplicate_detection(tmp_path):
    runtime = PresenceRuntime(heartbeat_interval=0.01, state_dir=tmp_path)
    await runtime.start()
    try:
        event = PresenceEvent(
            id="evt-fixed",
            type="test_signal",
            source="pytest",
            importance=1.0,
            urgency=1.0,
            goal_relevance=1.0,
            novelty=1.0,
            confidence=1.0,
            interruption_cost=0.0,
        )
        assert await runtime.submit(event) is True
        assert await runtime.submit(event) is False
        await asyncio.wait_for(runtime._queue.join(), timeout=1.0)
        await asyncio.sleep(0.02)

        state = runtime.status()
        assert state["runtime"] == "online"
        assert state["heartbeat_age_seconds"] is not None
        assert state["last_event_id"] == "evt-fixed"
        assert state["last_decision"]["decision"] == "SPEAK"
        assert runtime.recent()[0]["event_id"] == "evt-fixed"
        assert runtime.recent_events()[0]["id"] == "evt-fixed"
        # No explicit payload.message means the attention decision cannot invent speech.
        assert state["outbox_depth"] == 0
    finally:
        await runtime.stop()

    assert runtime.status()["runtime"] == "offline"


async def test_speak_event_with_explicit_message_reaches_outbox(tmp_path):
    runtime = PresenceRuntime(state_dir=tmp_path)
    await runtime.start()
    try:
        event = PresenceEvent(
            type="worker.connected",
            source="pytest",
            domain="system",
            importance=1.0,
            urgency=1.0,
            goal_relevance=1.0,
            novelty=1.0,
            confidence=1.0,
            interruption_cost=0.0,
            payload={"message": "BOYI, verified event received."},
        )
        assert await runtime.submit(event) is True
        await asyncio.wait_for(runtime._queue.join(), timeout=1.0)

        utterance = await runtime.next_utterance(wait=0.1)
        assert utterance is not None
        assert utterance.event_id == event.id
        assert utterance.trace_id == event.trace_id
        assert utterance.text == "BOYI, verified event received."
        assert runtime.status()["outbox_depth"] == 0
    finally:
        await runtime.stop()


async def test_silent_event_never_reaches_outbox(tmp_path):
    runtime = PresenceRuntime(state_dir=tmp_path)
    await runtime.start()
    try:
        event = PresenceEvent(
            type="low_signal",
            source="pytest",
            importance=0.0,
            urgency=0.0,
            goal_relevance=0.0,
            novelty=0.0,
            confidence=1.0,
            interruption_cost=1.0,
            payload={"message": "This must not be spoken."},
        )
        await runtime.submit(event)
        await asyncio.wait_for(runtime._queue.join(), timeout=1.0)
        assert await runtime.next_utterance(wait=0.01) is None
    finally:
        await runtime.stop()
