"""Coder connector — Scrappy hands coding/generation to Claude Code on the Mac.

Exposes a single server tool, `coder.code`, that Scrappy can call mid-conversation
whenever the moment calls for writing or generating code. It dispatches a
`claude -p "<prompt>"` run to the connected `scrappy worker` (via the worker
bridge) so the real work happens on Karnveer's Mac — with his files, repos, and
toolchain — and returns Claude Code's final output for Scrappy to relay.

If no worker is connected it returns a short instruction to start one, rather
than failing, so the conversation can continue.
"""

from __future__ import annotations

from typing import Any

from core.connectors.base import Connector, ConnectorManifest, InvocationContext, ToolSpec
from core.connectors.coder.builder import run_build
from core.logging import get_logger
from core.worker.bridge import get_worker_bridge

log = get_logger(__name__)

_DEFAULT_TIMEOUT = 240
_MIN_TIMEOUT = 30
_MAX_TIMEOUT = 600


class CoderConnector(Connector):
    manifest = ConnectorManifest(
        name="coder",
        version="0.1.0",
        description="Delegate coding and code-generation to Claude Code running on the Mac.",
        tools=[
            ToolSpec(
                name="code",
                description=(
                    "Run Claude Code (the `claude` CLI) HEADLESS on Karnveer's Mac "
                    "worker and get its final output back into this conversation — no "
                    "visible window. Use this for quick code generation when you just "
                    "need the result, or from the text CLI where there's no Mac screen. "
                    "When Karnveer is at his Mac and should WATCH Claude work in a "
                    "terminal, prefer mac.claude_code instead. Give ONE complete, "
                    "self-contained instruction (Claude cannot see this conversation). "
                    "Requires a connected `scrappy worker`."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": (
                                "Complete, standalone instruction for Claude Code — what "
                                "to build/change, in which project or path, and any "
                                "constraints."
                            ),
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Max seconds to wait (30–600, default 240).",
                        },
                    },
                    "required": ["prompt"],
                },
                executor="server",
                side_effects=["filesystem.write", "code.execute"],
            ),
            ToolSpec(
                name="build",
                description=(
                    "BUILD a complete, working deliverable end-to-end with Claude Code, "
                    "then report back and AUTO-OPEN the result. Give the GOAL in plain "
                    "words (e.g. 'a calculator web app'). Claude Code runs autonomously "
                    "to COMPLETION on the Mac worker — I wait for it, tell you in one "
                    "line what got built, and open it for you. Use this when Karnveer "
                    "wants the FINISHED thing (not to watch it code). Can take a few "
                    "minutes. Requires a connected `scrappy worker`."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "goal": {
                            "type": "string",
                            "description": (
                                "What to build, in plain words — the finished deliverable "
                                "you want (e.g. 'a working Pomodoro timer web app')."
                            ),
                        },
                    },
                    "required": ["goal"],
                },
                executor="server",
                side_effects=["filesystem.write", "code.execute", "app.open"],
            ),
        ],
    )

    async def invoke(self, action: str, args: dict, ctx: InvocationContext) -> Any:
        if action == "build":
            result = await run_build(
                str(args.get("goal", "")),
                bridge=get_worker_bridge(),
                session_id=ctx.session_id,
                on_progress=ctx.emit_progress,
            )
            log.info("coder.build", ok=result.get("ok"), opened=result.get("opened"))
            return result

        if action != "code":
            return f"error: unknown coder action {action!r}"

        prompt = str(args.get("prompt", "")).strip()
        if not prompt:
            return "error: empty prompt"
        timeout = min(max(int(args.get("timeout", _DEFAULT_TIMEOUT) or _DEFAULT_TIMEOUT),
                          _MIN_TIMEOUT), _MAX_TIMEOUT)

        bridge = get_worker_bridge()
        if not bridge.worker_online():
            return (
                "Claude Code runs on your Mac via the worker, which isn't connected. "
                "Start it in a terminal with `scrappy worker`, then ask me again."
            )

        agent_id = (str(ctx.session_id)[:8] if ctx.session_id else "scrappy")
        log.info("coder.dispatch", agent_id=agent_id, prompt=prompt[:160])
        return await bridge.submit(
            agent_id=agent_id,
            kind="claude",
            cmd=prompt,
            timeout=timeout,
            task=f"(coder) {prompt[:80]}",
        )
