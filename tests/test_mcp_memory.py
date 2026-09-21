from __future__ import annotations

import pytest

from apps.mcp.server import create_memory_mcp_server


@pytest.mark.asyncio
async def test_mcp_exposes_cross_platform_memory_tools() -> None:
    server = create_memory_mcp_server(lambda: None)  # tool bodies are not invoked here
    tools = await server.list_tools()
    names = {tool.name for tool in tools}

    assert {
        "vault_memory_context",
        "vault_memory_search",
        "vault_memory_remember",
        "vault_memory_recent",
        "vault_memory_forget",
        "vault_memory_stats",
    } <= names


@pytest.mark.asyncio
async def test_mcp_exposes_usage_resource() -> None:
    server = create_memory_mcp_server(lambda: None)
    resources = await server.list_resources()
    uris = {str(resource.uri) for resource in resources}

    assert "vault://memory/usage" in uris
