"""self.propose_change — Scrappy plans a self-change; Claude Code implements it.

Scrappy stays the orchestrator: he decides WHAT to change and hands the
implementation to Claude Code on the Mac (via the same worker bridge the `coder`
connector uses). Claude Code creates a `scrappy/self-<slug>` branch, makes the
edit, runs the tests, and reports the real `git diff --name-only` back. The
server then runs the guardrails (`check_change`) on that diff — the trusted
decision is made HERE, from git's ground truth, not from Claude Code's prose.

NOTHING is merged. The apply/merge onto main is a separate, approval-gated step
(IM-5.2); so is rollback (which needs the last-good tag apply creates).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Protocol

from core.identity.self_guard import check_change, protected_paths, self_modify_enabled
from core.logging import get_logger

log = get_logger(__name__)

BRANCH_PREFIX = "scrappy/self-"
_DEFAULT_TIMEOUT = 480
_MIN_TIMEOUT = 60
_MAX_TIMEOUT = 600


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
    ) -> str: ...


def slugify(goal: str, *, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")
    return s[:maxlen].rstrip("-") or "change"


def build_prompt(goal: str, branch: str) -> str:
    protected = "\n".join(f"  - {p}" for p in protected_paths())
    return f"""You are implementing a SELF-IMPROVEMENT to Scrappy's own codebase (the \
'itsmay' repository — Scrappy is a sovereign personal AI operator).

YOU ARE ALREADY INSIDE THAT REPOSITORY: it is your CURRENT WORKING DIRECTORY. Do
NOT clone it, do NOT search the filesystem for it, and do NOT `cd` anywhere else.
Work only here, on the files that are already around you.

GOAL:
{goal}

STEPS (do all, in order):
1. Confirm the working tree is clean (`git status`). If it is
   dirty, STOP and report that instead of changing anything.
2. Create and switch to a new branch:  git checkout -b {branch}
3. Implement the goal with minimal, focused changes that match the surrounding
   code's style. Add or update tests when it makes sense.
4. Do NOT modify any of these PROTECTED files — the change WILL be rejected if you
   touch them:
{protected}
5. Run tests + linter:  .venv/bin/python -m pytest -q  and  .venv/bin/ruff check .
   (fall back to `uv run pytest -q` / `uv run ruff check .` if that venv is absent).
6. Commit to {branch} if you like, but do NOT commit to main and do NOT merge.

Finally, output the REPORT as ONE JSON object on the LAST line (nothing after it),
with exactly these keys:
  - "branch": "{branch}"
  - "files_changed": array of paths from `git diff --name-only main...HEAD`
  - "tests_passed": true or false
  - "test_summary": the pytest summary line (e.g. "251 passed")
  - "notes": 1-2 sentences on what you changed
"""


@dataclass(slots=True)
class Proposal:
    ok: bool
    branch: str
    goal: str
    summary: str
    guard: dict | None = None  # check_change verdict on the reported diff
    files_changed: list[str] | None = None
    tests_passed: bool | None = None
    test_summary: str | None = None
    notes: str | None = None
    raw: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def extract_report(raw: str) -> dict | None:
    """Pull the report object out of Claude Code's output. Prefers a single-line
    JSON report (the instructed format); falls back to the largest brace span."""
    per_line = [
        ln.strip()
        for ln in raw.splitlines()
        if ln.strip().startswith("{") and ln.strip().endswith("}")
    ]
    # Try the last single-line report first (the instructed format), then earlier
    # lines, then the whole first-{-to-last-} span (multi-line JSON) as a fallback.
    ordered = list(reversed(per_line))
    span = re.search(r"\{.*\}", raw, re.DOTALL)
    if span:
        ordered.append(span.group(0))
    for blob in ordered:
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "branch" in obj:
            return obj
    return None


def _summarize(ok: bool, verdict, tests_passed: bool, files: list[str]) -> str:
    if ok:
        return (
            f"Proposed on branch — {len(files)} file(s) changed, tests passed, "
            "guardrails OK. Review the diff; approve to apply (I won't merge to main "
            "without your go-ahead)."
        )
    problems = []
    if not verdict.allowed:
        problems.append(f"guardrails BLOCK it — {verdict.reason}")
    if not tests_passed:
        problems.append("tests did NOT pass")
    if not problems:
        problems.append("the report was incomplete")
    return (
        "This proposal is NOT ready to apply: "
        + "; ".join(problems)
        + ". The branch exists for inspection; nothing was merged."
    )


async def propose_change(
    goal: str,
    *,
    bridge: _Bridge,
    session_id: str | None = None,
    timeout: int = _DEFAULT_TIMEOUT,  # noqa: ASYNC109 - mirrors the bridge API
) -> Proposal:
    """Dispatch a self-change to Claude Code on a branch and guard the result.

    Never merges. Refuses when self-modification is frozen/off or no worker is
    connected. The returned `guard` verdict is computed server-side from the
    reported `git diff` — protected files (auth/approval/keypool/config/guard)
    make it fail even if Claude Code's tests pass.
    """
    goal = (goal or "").strip()
    if not goal:
        return Proposal(ok=False, branch="", goal="", summary="error: empty goal")
    if not self_modify_enabled():
        return Proposal(
            ok=False,
            branch="",
            goal=goal,
            summary=(
                "Self-modification is disabled or frozen (SELF_MODIFY / `scrappy "
                "freeze`). Unfreeze before I can propose changes to myself."
            ),
        )
    if not bridge.worker_online():
        return Proposal(
            ok=False,
            branch="",
            goal=goal,
            summary=(
                "I need Claude Code on your Mac to implement this, but the worker "
                "isn't connected. Start it with `scrappy worker`, then ask me again."
            ),
        )

    branch = f"{BRANCH_PREFIX}{slugify(goal)}"
    timeout = min(max(int(timeout or _DEFAULT_TIMEOUT), _MIN_TIMEOUT), _MAX_TIMEOUT)
    agent_id = str(session_id)[:8] if session_id else "scrappy"
    log.info("self.propose.dispatch", branch=branch, goal=goal[:120])
    raw = await bridge.submit(
        agent_id=agent_id,
        kind="claude",
        cmd=build_prompt(goal, branch),
        timeout=timeout,
        task=f"(self) {goal[:70]}",
        workdir="repo",  # must run inside the real checkout, not a scratch dir
    )

    report = extract_report(raw)
    if report is None:
        return Proposal(
            ok=False,
            branch=branch,
            goal=goal,
            summary=(
                "Claude Code ran but I couldn't parse a structured report from it. "
                "Inspect the branch manually before doing anything else."
            ),
            raw=raw[-1500:],
        )

    files = [str(f) for f in (report.get("files_changed") or []) if str(f).strip()]
    # Path policy only — the frozen/off gate is already handled above.
    verdict = check_change(files, enabled=True)
    tests_passed = bool(report.get("tests_passed"))
    ok = verdict.allowed and tests_passed and bool(files)
    return Proposal(
        ok=ok,
        branch=str(report.get("branch") or branch),
        goal=goal,
        summary=_summarize(ok, verdict, tests_passed, files),
        guard=verdict.to_dict(),
        files_changed=files,
        tests_passed=tests_passed,
        test_summary=str(report.get("test_summary") or "") or None,
        notes=str(report.get("notes") or "") or None,
        raw=raw[-800:],
    )
