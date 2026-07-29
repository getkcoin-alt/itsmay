"""coder.build — Scrappy briefs Claude Code, waits for the whole build, reports + opens.

Unlike `coder.code` (raw output relayed back) and `mac.claude_code` (a live window
Boss watches), `coder.build` drives a HEADLESS Claude Code run to COMPLETION on the
Mac worker, parses a structured `SCRAPPY_RESULT` line, and auto-opens the finished
result — so "build me a calculator" becomes a working, opened calculator with a
one-line report, no babysitting.

Blocking by design (it extends the proven `coder.code` request/response over the
worker bridge): waiting for the worker's result IS the monitoring. It also runs
in streaming mode — the worker POSTs live milestones ("Writing index.html",
"Running: npm install") back over the progress channel, so instead of a silent
multi-minute block, Boss hears Claude Code think and work in real time.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable
from typing import Protocol

from core.logging import get_logger

log = get_logger(__name__)

BUILD_TIMEOUT = 600  # up to 10 min for a full build (bridge max)
OPEN_TIMEOUT = 20
_RESULT_RE = re.compile(r"SCRAPPY_RESULT:\s*(\{.*\})")
# Shell metacharacters that must never appear in an auto-run open target.
_UNSAFE = set(";&|`$<>()\n\r")


class _Bridge(Protocol):
    def worker_online(self) -> bool: ...
    async def submit(
        self,
        *,
        agent_id: str,
        kind: str,
        cmd: str,
        timeout: int,  # noqa: ASYNC109 - mirrors WorkerBridge.submit's queue API
        task: str = ...,
        stream_progress: bool = ...,
        on_progress: Callable[[str], None] | None = ...,
    ) -> str: ...


def build_prompt(goal: str) -> str:
    """Brief Claude Code to build to completion and end with a machine-parsable line."""
    return f"""You are building a COMPLETE, working deliverable for the user, end to end, in \
the current directory. Do the ENTIRE job autonomously — set it up, write all the code, \
install what's needed, and make it actually run. Do not stop half-way or ask questions.

TASK: {goal}

When it is finished and working, print EXACTLY ONE final line, and nothing after it:
SCRAPPY_RESULT: {{"ok": true, "summary": "what you built", "open": "file/app/URL or empty"}}
`open` = a single file, app name, or URL that opens the result (e.g. index.html), or "".
If you could NOT finish, print instead:
SCRAPPY_RESULT: {{"ok": false, "summary": "what's blocking", "open": ""}}
"""


def parse_result(output: str) -> dict | None:
    """Pull the last valid `SCRAPPY_RESULT: {...}` object out of Claude Code's output."""
    for blob in reversed(_RESULT_RE.findall(output or "")):
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "ok" in obj:
            return obj
    return None


def _safe_target(target: str) -> bool:
    """A file/app/URL safe to pass to `open` — no shell metacharacters, bounded length."""
    target = (target or "").strip()
    return bool(target) and len(target) < 300 and not (_UNSAFE & set(target))


async def run_build(
    goal: str,
    *,
    bridge: _Bridge,
    session_id: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Build `goal` with Claude Code to completion, then open the result. Returns
    `{ok, summary, opened, open_target}` (or `needs_worker` / `raw` on failure).

    `on_progress`, when given, receives live milestones — both the worker's
    streamed play-by-play and our own ("Opening it now") — for Scrappy to speak.
    """

    def _emit(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception as e:  # narration must never break the build
                log.warning("coder.build.emit_failed", err=str(e))

    goal = (goal or "").strip()
    if not goal:
        return {"ok": False, "summary": "error: empty goal"}
    if not bridge.worker_online():
        return {
            "ok": False,
            "needs_worker": True,
            "summary": (
                "I need Claude Code on your Mac to build this — start `scrappy worker`, "
                "then ask me again."
            ),
        }

    agent_id = str(session_id)[:8] if session_id else "scrappy"
    log.info("coder.build.dispatch", goal=goal[:120])
    out = await bridge.submit(
        agent_id=agent_id,
        kind="claude",
        cmd=build_prompt(goal),
        timeout=BUILD_TIMEOUT,
        task=f"(build) {goal[:70]}",
        stream_progress=True,
        on_progress=on_progress,
    )

    report = parse_result(out)
    if report is None:
        return {
            "ok": False,
            "summary": "Claude Code ran but didn't report a clean result — check the workspace.",
            "raw": out[-800:],
        }

    ok = bool(report.get("ok"))
    summary = str(report.get("summary") or "").strip()
    target = str(report.get("open") or "").strip()

    opened = False
    if ok and _safe_target(target):
        _emit(f"Opening {target}")
        try:
            # Runs in the same worker workspace dir the build wrote to.
            await bridge.submit(
                agent_id=agent_id,
                kind="bash",
                cmd=f"open {shlex.quote(target)}",
                timeout=OPEN_TIMEOUT,
                task="(build) open result",
            )
            opened = True
        except Exception as e:
            log.warning("coder.build.open_failed", err=str(e))

    log.info("coder.build.done", ok=ok, opened=opened)
    return {
        "ok": ok,
        "summary": summary or ("built" if ok else "not finished"),
        "opened": opened,
        "open_target": target or None,
    }
