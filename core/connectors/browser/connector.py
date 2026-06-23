"""Browser connector — read and watch Google Chrome tabs from the Mac.

All four tools execute on the Mac client (executor="client_mac"); the
server only describes them to the LLM and routes call events to the
voice agent over SSE. The Mac side runs chrome.py + tab_watcher.py.
"""

from __future__ import annotations

from core.connectors.base import Connector, ConnectorManifest, InvocationContext, ToolSpec

_BROWSER_TOOLS = [
    ToolSpec(
        name="list_tabs",
        description=(
            "List all open Google Chrome tabs across every window. "
            "Returns window index, tab index, title, and URL for each tab. "
            "Call this before read_tab or watch_start to get the indices."
        ),
        executor="client_mac",
        parameters={"type": "object", "properties": {}, "required": []},
    ),
    ToolSpec(
        name="read_tab",
        description=(
            "Read the full visible text content of a specific Chrome tab. "
            "Use list_tabs first to get the win/tab indices."
        ),
        executor="client_mac",
        parameters={
            "type": "object",
            "properties": {
                "win": {"type": "integer", "description": "Window index from list_tabs."},
                "tab": {"type": "integer", "description": "Tab index from list_tabs."},
            },
            "required": ["win", "tab"],
        },
    ),
    ToolSpec(
        name="watch_start",
        description=(
            "Start monitoring a Chrome tab for content changes. "
            "Every `interval` seconds (default 120) it checks for new content; "
            "when something changes it sends an AI summary as a macOS notification. "
            "Use list_tabs first to get the win/tab indices."
        ),
        executor="client_mac",
        parameters={
            "type": "object",
            "properties": {
                "win": {"type": "integer", "description": "Window index from list_tabs."},
                "tab": {"type": "integer", "description": "Tab index from list_tabs."},
                "interval": {
                    "type": "integer",
                    "description": "Poll interval in seconds. Default 120.",
                    "default": 120,
                },
            },
            "required": ["win", "tab"],
        },
    ),
    ToolSpec(
        name="watch_stop",
        description="Stop monitoring the currently watched Chrome tab.",
        executor="client_mac",
        parameters={"type": "object", "properties": {}, "required": []},
    ),
]


class BrowserConnector(Connector):
    manifest = ConnectorManifest(
        name="browser",
        version="0.1.0",
        description="Read and watch Google Chrome tabs from the Mac.",
        tools=_BROWSER_TOOLS,
    )

    async def invoke(self, action: str, args: dict, ctx: InvocationContext) -> str:
        raise NotImplementedError("browser tools run on the Mac client, not the server")
