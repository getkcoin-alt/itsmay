"""Self-modification must run inside Scrappy's OWN checkout.

The bug this pins down: every self.* command was dispatched without a working
directory, so the worker ran it in the per-agent scratch dir (~/scrappy-workspace/
<id>) — empty and not a git repo. Claude Code landed in a bare folder, tried to
clone, got blocked, and reported "no changes made / not a git repository".
"""

from __future__ import annotations

from pathlib import Path

import pytest

import apps.cli as cli
from core.identity.propose import build_prompt


class _Bridge:
    """Captures what the self.* paths submit to the worker."""

    def __init__(self, output: str = "") -> None:
        self.output = output
        self.calls: list[dict] = []

    def worker_online(self) -> bool:
        return True

    async def submit(self, **kw):
        self.calls.append(kw)
        return self.output


# ── dispatch carries workdir="repo" ───────────────────────────────────


async def test_propose_runs_in_the_repo():
    from core.identity.propose import propose_change

    bridge = _Bridge("no report line")
    await propose_change("add a thing", bridge=bridge, session_id="s")
    assert bridge.calls, "nothing was dispatched"
    assert bridge.calls[0]["workdir"] == "repo"
    assert bridge.calls[0]["kind"] == "claude"


async def test_apply_preflight_and_script_run_in_the_repo():
    from core.identity.apply import apply_change

    # A real proposal branch (apply only accepts its own prefix), and a diff
    # naming one safe file so the guard lets it reach the apply step.
    bridge = _Bridge("README.md")
    await apply_change("scrappy/self-tidy-thing", bridge=bridge)
    assert bridge.calls, "nothing was dispatched"
    # Both the diff preflight and the apply script need the real repo.
    assert all(c["workdir"] == "repo" for c in bridge.calls)


async def test_rollback_runs_in_the_repo():
    from core.identity.apply import rollback

    bridge = _Bridge("ROLLBACK_FAILED")
    await rollback(bridge=bridge)
    assert bridge.calls and bridge.calls[0]["workdir"] == "repo"


# ── the prompt no longer sends Claude Code hunting ────────────────────


def test_prompt_says_it_is_already_in_the_repo():
    p = build_prompt("do a thing", "feature/thing")
    assert "CURRENT WORKING DIRECTORY" in p
    # Normalise whitespace — the instruction wraps across lines in the prompt.
    low = " ".join(p.split()).lower()
    assert "do not clone" in low
    assert "do not search the filesystem" in low


# ── worker-side resolution ────────────────────────────────────────────


def test_repo_dir_prefers_explicit_env(monkeypatch, tmp_path):
    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("SCRAPPY_REPO_DIR", str(repo))
    assert cli._repo_dir() == repo


def test_repo_dir_rejects_a_path_that_is_not_a_repo(monkeypatch, tmp_path):
    not_repo = tmp_path / "plain"
    not_repo.mkdir()
    monkeypatch.setenv("SCRAPPY_REPO_DIR", str(not_repo))
    assert cli._repo_dir() is None


def test_repo_dir_accepts_git_file_for_worktrees(monkeypatch, tmp_path):
    # In a git worktree, `.git` is a FILE pointing at the real gitdir.
    repo = tmp_path / "wt"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt")
    monkeypatch.setenv("SCRAPPY_REPO_DIR", str(repo))
    assert cli._repo_dir() == repo


def test_repo_dir_falls_back_to_the_installed_checkout(monkeypatch):
    monkeypatch.delenv("SCRAPPY_REPO_DIR", raising=False)
    # This test suite runs from the repo, so the walk-up must find it.
    assert cli._repo_dir() == Path(cli.__file__).resolve().parents[2]


# ── worker routing ────────────────────────────────────────────────────


def test_worker_runs_repo_commands_in_the_checkout(monkeypatch, tmp_path):
    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("SCRAPPY_REPO_DIR", str(repo))
    seen: dict = {}

    class _Proc:
        stdout, stderr, returncode = "on-branch", "", 0

    def fake_run(argv, **kw):
        seen.update(kw)
        return _Proc()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    out = cli._run_local_command("bash", "git status", 30, "a1", workdir_hint="repo")
    assert out == "on-branch"
    assert seen["cwd"] == str(repo)


def test_worker_reports_clearly_when_the_repo_is_missing(monkeypatch):
    monkeypatch.setenv("SCRAPPY_REPO_DIR", "/nope/not/here")
    out = cli._run_local_command("bash", "git status", 30, "a1", workdir_hint="repo")
    # Must say what's wrong instead of silently running in an empty scratch dir.
    assert "can't find Scrappy's own repo" in out
    assert "SCRAPPY_REPO_DIR" in out


def test_ordinary_commands_still_use_the_scratch_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "WORKSPACE", tmp_path / "ws")
    seen: dict = {}

    class _Proc:
        stdout, stderr, returncode = "ok", "", 0

    def fake_run(argv, **kw):
        seen.update(kw)
        return _Proc()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli._run_local_command("bash", "echo hi", 30, "a1")
    assert seen["cwd"] == str(tmp_path / "ws" / "a1")


# ── payload plumbing ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workdir_reaches_the_worker_payload():
    import asyncio

    from core.worker.bridge import WorkerBridge

    bridge = WorkerBridge()

    async def worker():
        cmd = await bridge.next_command(wait=2.0)
        assert cmd.to_payload()["workdir"] == "repo"
        bridge.complete(cmd.id, "done")

    task = asyncio.create_task(worker())
    await bridge.submit(agent_id="a", kind="bash", cmd="git status", timeout=2, workdir="repo")
    await task
