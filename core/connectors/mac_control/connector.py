"""Mac control connector.

Server-side this is just a manifest — the actual execution happens on the
Mac inside the voice agent, which listens for `tool_call` SSE events from
`/v1/chat`. Keeping the manifest server-side lets the LLM see the tools and
emit structured `tool_calls`; the chat router then forwards the call to the
client because the executor is marked `client_mac`.

When the Mac is offline (no voice loop running) these tools are simply
unreachable. That's an acceptable limitation for v1; v2 will run a
persistent Mac daemon that holds a WebSocket back to the server.
"""

from __future__ import annotations

from typing import Any

from core.connectors.base import Connector, ConnectorManifest, InvocationContext, ToolSpec

_MAC_TOOLS = [
    ToolSpec(
        name="open_app",
        description=(
            "Open or focus a macOS application by name. Use the exact app name "
            "as it appears in /Applications (e.g. 'Safari', 'Google Chrome', "
            "'Visual Studio Code', 'Slack', 'Notes')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Application name as shown in /Applications.",
                },
            },
            "required": ["name"],
        },
        executor="client_mac",
        side_effects=["mac.window_focus"],
    ),
    ToolSpec(
        name="open_url",
        description=(
            "Open a URL in the user's default web browser. Use for sending the "
            "user to a webpage (signup form, dashboard, search result, etc)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Fully-qualified https:// URL."},
            },
            "required": ["url"],
        },
        executor="client_mac",
        side_effects=["mac.browser_open"],
    ),
    ToolSpec(
        name="notify",
        description=(
            "Show a macOS notification banner. Use for time-sensitive nudges "
            "the user shouldn't miss (incoming task complete, reminder, etc)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["title", "body"],
        },
        executor="client_mac",
        side_effects=["mac.notification"],
    ),
    ToolSpec(
        name="say",
        description=(
            "Speak text aloud using the built-in macOS `say` command. "
            "Useful for quick confirmations without going through ElevenLabs."
        ),
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        executor="client_mac",
        side_effects=["mac.audio_out"],
    ),
    ToolSpec(
        name="claude_code",
        description=(
            "Open a NEW Terminal window on the Mac and start Claude Code (the "
            "`claude` CLI) on a coding task — Karnveer watches it work live and can "
            "jump in. THIS is how you write or change software when he asks you to "
            "code, build, generate, fix, or refactor anything. YOU author the "
            "prompt: make it a complete, refined, self-contained brief — what to "
            "build, the language/framework, the working directory or project, and "
            "any constraints — because Claude Code cannot see this conversation. "
            "Prefer this over coder.code whenever Karnveer is at his Mac."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "Complete, polished instruction for Claude Code — written by "
                        "you, ready to run with no further context."
                    ),
                },
                "dir": {
                    "type": "string",
                    "description": "Working directory (default ~/scrappy-workspace).",
                },
            },
            "required": ["prompt"],
        },
        executor="client_mac",
        side_effects=["mac.terminal_open", "code.execute"],
    ),
    ToolSpec(
        name="run_applescript",
        description=(
            "Run an arbitrary AppleScript snippet on the user's Mac. POWERFUL — "
            "can open apps, control windows, query system state, send keystrokes. "
            "Use only when the more specific tools above don't fit."
        ),
        parameters={
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "AppleScript source. Avoid `osascript` shell-out — just pass the script body.",
                },
            },
            "required": ["script"],
        },
        executor="client_mac",
        requires_approval=True,
        side_effects=["mac.arbitrary_execution"],
    ),
]


class MacControlConnector(Connector):
    manifest = ConnectorManifest(
        name="mac",
        version="0.1.0",
        description="Direct control of the user's MacBook (executed on the Mac client).",
        tools=_MAC_TOOLS,
    )

    async def invoke(self, action: str, args: dict, ctx: InvocationContext) -> Any:
        # All mac.* tools are client-executed; the registry won't dispatch
        # here, but if something misroutes we fail loud.
        raise NotImplementedError(
            f"mac.{action} runs on the Mac client, not the server"
        )
