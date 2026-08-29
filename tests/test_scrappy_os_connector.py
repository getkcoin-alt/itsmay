from __future__ import annotations

import json

import httpx
import pytest

from core.connectors.base import InvocationContext
from core.connectors.scrappy_os.connector import ScrappyOSConnector


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


def test_bridge_exposes_no_mutating_objective_tool() -> None:
    names = {tool.name for tool in ScrappyOSConnector.manifest.tools}
    assert names == {"health", "submit_read_objective", "task_status"}
    assert "submit_objective" not in names
