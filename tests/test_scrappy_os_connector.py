from __future__ import annotations

import json
from uuid import UUID, uuid4

import httpx
import pytest

from core.connectors.base import InvocationContext
from core.connectors.scrappy_os.connector import ScrappyOSConnector


class FakeExperienceStore:
    def __init__(self) -> None:
        self.items: list[tuple[object, UUID | None]] = []
        self.id = uuid4()

    async def put(self, item: object, *, user_id: UUID | None = None):
        self.items.append((item, user_id))
        return {"id": self.id}, True


@pytest.mark.asyncio
async def test_submit_objective_is_hard_capped_to_read_risk() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        seen["correlation"] = request.headers.get("x-syncbond-correlation-id")
        seen["version"] = request.headers.get("x-syncbond-version")
        return httpx.Response(
            202,
            json={
                "objective_id": "9b342238-7d08-4a5f-a647-6bcd2acbbfde",
                "status": "accepted",
                "events_url": "/tasks/9b342238-7d08-4a5f-a647-6bcd2acbbfde/events",
                "correlation_id": seen["correlation"],
                "syncbond_version": "5.0.0",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = ScrappyOSConnector(
            base_url="http://scrappy-os.local:8787",
            token="dedicated-vault-token",
            client=client,
        )
        result = await connector.invoke(
            "submit_read_objective",
            {
                "objective": "Inspect disk usage and report evidence",
                "success_criteria": ["identify the fullest filesystem"],
            },
            InvocationContext(user_id="karnveer", session_id="session-1"),
        )

    assert seen["method"] == "POST"
    assert seen["path"] == "/tasks"
    assert seen["body"] == {
        "objective": "Inspect disk usage and report evidence",
        "max_risk": "read",
        "dry_run": False,
    }
    assert seen["auth"] == "Bearer dedicated-vault-token"
    assert seen["correlation"]
    assert seen["version"] == "5.0.0"
    assert result["syncbond"]["event_type"] == "objective.requested"
    assert result["syncbond"]["correlation_id"] == seen["correlation"]
    assert result["syncbond"]["payload"]["max_risk"] == "read"
    assert result["remote_objective_id"] == "9b342238-7d08-4a5f-a647-6bcd2acbbfde"


@pytest.mark.asyncio
async def test_submit_rejects_mismatched_remote_correlation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202,
            json={
                "objective_id": str(uuid4()),
                "status": "accepted",
                "correlation_id": str(uuid4()),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = ScrappyOSConnector(
            base_url="http://scrappy-os.local:8787",
            token="token",
            client=client,
        )
        with pytest.raises(RuntimeError, match="mismatched correlation"):
            await connector.invoke(
                "submit_read_objective",
                {"objective": "Inspect disk usage"},
                InvocationContext(),
            )


@pytest.mark.asyncio
async def test_health_does_not_require_a_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert request.headers.get("authorization") is None
        return httpx.Response(
            200,
            json={
                "healthy": True,
                "status": "healthy",
                "version": "0.2.0",
                "uptime_seconds": 12.5,
                "components": [{"secret": "must not be copied blindly"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = ScrappyOSConnector(
            base_url="http://scrappy-os.local:8787",
            client=client,
        )
        result = await connector.invoke("health", {}, InvocationContext())

    assert result == {
        "healthy": True,
        "status": "healthy",
        "version": "0.2.0",
        "uptime_seconds": 12.5,
    }


@pytest.mark.asyncio
async def test_task_status_rejects_path_injection() -> None:
    connector = ScrappyOSConnector(
        base_url="http://scrappy-os.local:8787",
        token="token",
    )
    with pytest.raises(ValueError, match="UUID"):
        await connector.invoke(
            "task_status",
            {"objective_id": "../../approvals"},
            InvocationContext(),
        )


@pytest.mark.asyncio
async def test_record_terminal_experience_requires_matching_remote_evidence() -> None:
    remote_id = uuid4()
    correlation_id = uuid4()
    user_id = uuid4()
    store = FakeExperienceStore()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/tasks/{remote_id}"
        return httpx.Response(
            200,
            json={
                "objective_id": str(remote_id),
                "correlation_id": str(correlation_id),
                "state": "completed",
                "succeeded": True,
                "conclusion": "model prose is not evidence",
                "steps": [
                    {
                        "tool": "system.disk",
                        "risk": "read",
                        "decision": "allow",
                        "success": True,
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = ScrappyOSConnector(
            base_url="http://scrappy-os.local:8787",
            token="token",
            client=client,
            experience_store=store,  # type: ignore[arg-type]
        )
        result = await connector.invoke(
            "record_terminal_experience",
            {"objective_id": str(remote_id), "correlation_id": str(correlation_id)},
            InvocationContext(user_uuid=user_id),
        )

    assert result["created"] is True
    assert result["outcome"] == "succeeded"
    assert result["syncbond"]["event_type"] == "experience.recorded"
    assert result["syncbond"]["correlation_id"] == str(correlation_id)
    assert len(store.items) == 1
    assert store.items[0][1] == user_id


def test_bridge_has_no_mutating_machine_objective_and_gates_memory_write() -> None:
    by_name = {tool.name: tool for tool in ScrappyOSConnector.manifest.tools}
    assert set(by_name) == {
        "health",
        "submit_read_objective",
        "task_status",
        "record_terminal_experience",
    }
    assert "submit_objective" not in by_name
    assert by_name["submit_read_objective"].requires_approval is False
    assert by_name["record_terminal_experience"].requires_approval is True
    assert "durable" in " ".join(by_name["record_terminal_experience"].side_effects)
