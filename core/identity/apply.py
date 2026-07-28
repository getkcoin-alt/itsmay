"""self.apply_change / self.rollback — the moment Scrappy changes himself.

`apply_change` is the approval-gated end of the loop (propose → apply). It is
deliberately DETERMINISTIC — a fixed git script run on the Mac worker, not
Claude Code's judgment — because merging to main must be mechanical and
auditable:

  1. Ask the worker for the branch's real diff (`git diff --name-only`).
  2. Guard it SERVER-SIDE (check_change) — authoritative, before anything merges.
  3. Tag the current main as `last-good`, merge the branch (authored as Scrappy),
     run the tests. If they fail, reset to `last-good` automatically.
  4. Write an audit memory ("On <date> I changed X") so he remembers his own
     evolution.

The approval chokepoint is the registry gate (#13): `apply_change` is
`requires_approval=True`, so the model can never self-approve — the operator
does. `rollback` resets main to `last-good`; it's NOT approval-gated (recovery
must be fast, and the supervisor will call it automatically on a bad restart).

Both refuse when self-modification is frozen/off. The merge is LOCAL (no push);
the supervised restart + HTTP health-probe is the remaining Mac-side wiring.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from core.identity.propose import BRANCH_PREFIX, _Bridge
from core.identity.self_guard import check_change, self_modify_enabled
from core.logging import get_logger

log = get_logger(__name__)

_DIFF_TIMEOUT = 30
_APPLY_TIMEOUT = 600
_ROLLBACK_TIMEOUT = 60

_SCRAPPY_AUTHOR = "-c user.name='Scrappy Singh' -c user.email='scrappy@itsmay.local'"


def build_apply_script(branch: str, message: str) -> str:
    """Deterministic apply: tag last-good → merge (as Scrappy) → test → auto-rollback.

    Prints exactly one terminal marker: APPLY_OK <sha> | APPLY_ROLLED_BACK <why> |
    APPLY_FAILED <why>. Runs in the worker's cwd (the itsmay repo). No push.
    """
    safe_msg = message.replace("'", "").replace("\n", " ")[:200]
    return f"""\
git checkout main >/dev/null 2>&1 || {{ echo "APPLY_FAILED checkout-main"; exit 0; }}
git tag -f last-good HEAD >/dev/null 2>&1
if git {_SCRAPPY_AUTHOR} merge --no-ff {branch} -m '{safe_msg}' >/dev/null 2>&1; then
  if .venv/bin/python -m pytest -q >/tmp/scrappy_apply.log 2>&1 \
     || uv run pytest -q >/tmp/scrappy_apply.log 2>&1; then
    echo "APPLY_OK $(git rev-parse --short HEAD)"
  else
    git reset --hard last-good >/dev/null 2>&1
    echo "APPLY_ROLLED_BACK tests-failed"
  fi
else
  git merge --abort >/dev/null 2>&1
  echo "APPLY_FAILED merge-conflict"
fi
"""


def build_rollback_script() -> str:
    return """\
git checkout main >/dev/null 2>&1 || { echo "ROLLBACK_FAILED checkout-main"; exit 0; }
if git rev-parse last-good >/dev/null 2>&1; then
  git reset --hard last-good >/dev/null 2>&1 && echo "ROLLED_BACK $(git rev-parse --short HEAD)"
else
  echo "ROLLBACK_FAILED no-last-good-tag"
fi
"""


def _marker(raw: str, prefixes: tuple[str, ...]) -> str | None:
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if line.startswith(prefixes):
            return line
    return None


@dataclass(slots=True)
class ApplyResult:
    applied: bool
    branch: str
    summary: str
    guard: dict | None = None
    files_changed: list[str] | None = None
    outcome: str | None = None  # the worker marker line
    restart_needed: bool = False

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(slots=True)
class RollbackResult:
    ok: bool
    summary: str
    outcome: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


async def _write_audit(semantic, embedder, user_id, *, branch, files, notes) -> None:
    if not (semantic and embedder and user_id):
        return
    day = datetime.now(UTC).date().isoformat()
    detail = notes.strip() or "an improvement to myself"
    content = (
        f"On {day} I changed my own code: {detail}. Applied branch '{branch}' "
        f"to main ({len(files)} file(s): {', '.join(files[:8])})."
    )
    try:
        emb = await embedder.embed(content)
        await semantic.write(
            user_id, "reflection", content, emb, source="self-apply", importance=0.85
        )
    except Exception as e:
        log.warning("self.apply.audit_failed", err=str(e))


async def apply_change(
    branch: str,
    *,
    bridge: _Bridge,
    notes: str = "",
    semantic=None,
    embedder=None,
    user_id=None,
    session_id: str | None = None,
) -> ApplyResult:
    """Merge a proposed self-change to main, gated + guarded + auto-rolled-back.

    Guard is authoritative and server-side: the branch's real diff is fetched
    from the worker and run through check_change BEFORE anything merges. A diff
    touching a protected file (auth/approval/keypool/config/guard) is refused
    even with operator approval.
    """
    branch = (branch or "").strip()
    if not branch.startswith(BRANCH_PREFIX):
        return ApplyResult(
            applied=False,
            branch=branch,
            summary=f"I only apply my own proposal branches ('{BRANCH_PREFIX}*').",
        )
    if not self_modify_enabled():
        return ApplyResult(
            applied=False, branch=branch, summary="Self-modification is disabled or frozen."
        )
    if not bridge.worker_online():
        return ApplyResult(
            applied=False,
            branch=branch,
            summary="The Mac worker isn't connected — start `scrappy worker` and try again.",
        )

    agent_id = str(session_id)[:8] if session_id else "scrappy"
    # 1. Ground-truth diff from the worker.
    diff_raw = await bridge.submit(
        agent_id=agent_id,
        kind="bash",
        cmd=f"git diff --name-only main...{branch}",
        timeout=_DIFF_TIMEOUT,
        task="(self) apply preflight diff",
    )
    files = [ln.strip() for ln in diff_raw.splitlines() if ln.strip()]

    # 2. Authoritative guard (path policy; frozen already handled above).
    verdict = check_change(files, enabled=True)
    if not verdict.allowed:
        log.warning("self.apply.blocked", branch=branch, reason=verdict.reason)
        return ApplyResult(
            applied=False,
            branch=branch,
            summary=f"Refused: {verdict.reason}. Nothing was merged.",
            guard=verdict.to_dict(),
            files_changed=files,
        )

    # 3. Deterministic apply on the worker.
    message = f"Scrappy self-change ({branch}): {notes or 'applied via self.apply_change'}"
    out = await bridge.submit(
        agent_id=agent_id,
        kind="bash",
        cmd=build_apply_script(branch, message),
        timeout=_APPLY_TIMEOUT,
        task=f"(self) apply {branch}",
    )
    marker = _marker(out, ("APPLY_OK", "APPLY_ROLLED_BACK", "APPLY_FAILED"))
    applied = bool(marker and marker.startswith("APPLY_OK"))

    if applied:
        await _write_audit(semantic, embedder, user_id, branch=branch, files=files, notes=notes)
        summary = (
            f"Applied to main ({len(files)} file(s)), tests green. Restart me to run "
            "as the new version. Use self.rollback if anything's off."
        )
    elif marker and marker.startswith("APPLY_ROLLED_BACK"):
        summary = (
            "Merged but post-merge tests FAILED — auto-rolled back to last-good. "
            "Main is unchanged."
        )
    else:
        summary = (
            f"Apply did not complete ({marker or 'no marker from worker'}). "
            "Main is unchanged."
        )

    log.info("self.apply", branch=branch, applied=applied, marker=marker)
    return ApplyResult(
        applied=applied,
        branch=branch,
        summary=summary,
        guard=verdict.to_dict(),
        files_changed=files,
        outcome=marker,
        restart_needed=applied,
    )


async def rollback(*, bridge: _Bridge, session_id: str | None = None) -> RollbackResult:
    """Reset main to the `last-good` tag (the pre-apply state). Freeze-gated,
    not approval-gated — recovery must be immediate."""
    if not self_modify_enabled():
        return RollbackResult(ok=False, summary="Self-modification is disabled or frozen.")
    if not bridge.worker_online():
        return RollbackResult(ok=False, summary="The Mac worker isn't connected.")

    out = await bridge.submit(
        agent_id=str(session_id)[:8] if session_id else "scrappy",
        kind="bash",
        cmd=build_rollback_script(),
        timeout=_ROLLBACK_TIMEOUT,
        task="(self) rollback to last-good",
    )
    marker = _marker(out, ("ROLLED_BACK", "ROLLBACK_FAILED"))
    ok = bool(marker and marker.startswith("ROLLED_BACK"))
    summary = (
        "Rolled main back to last-good. Restart me to run the restored version."
        if ok
        else f"Rollback failed ({marker or 'no marker'})."
    )
    log.info("self.rollback", ok=ok, marker=marker)
    return RollbackResult(ok=ok, summary=summary, outcome=marker)
