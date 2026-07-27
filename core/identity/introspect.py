"""Repo introspection for `self.describe` — read-only 'what is my code right now'.

Answers the question Scrappy asks before proposing any change to himself: what
branch am I on, is the tree clean, what have I changed lately, what's off-limits,
and can I even self-modify right now? Pure read-only (git + fs), safe to call
anytime — including while frozen.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from core.identity.self_guard import is_frozen, protected_paths, self_modify_enabled
from core.identity.self_model import repo_root, version_str

# Top-level dirs worth surfacing even if they aren't import packages.
_KNOWN_TOP = {"apps", "core", "tests", "docs"}


def _git(root: Path, *args: str, timeout: int = 3) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=timeout, cwd=root
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def _top_level(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir()
        and not p.name.startswith(".")
        and (p.name in _KNOWN_TOP or (p / "__init__.py").exists())
    )


def describe_self() -> dict[str, Any]:
    """A read-only snapshot of Scrappy's own codebase. Synchronous (git + fs);
    connectors call it via `asyncio.to_thread` to stay off the event loop."""
    root = repo_root()
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    status = _git(root, "status", "--porcelain")
    dirty = [line[3:] for line in status.splitlines()] if status else []
    log = _git(root, "log", "-5", "--pretty=%h %s")
    commits = log.splitlines() if log else []

    return {
        "version": version_str(),
        "repo_root": str(root),
        "branch": branch,
        "clean": not dirty,
        "dirty_files": dirty[:50],
        "recent_commits": commits,
        "top_level": _top_level(root),
        "protected_paths": protected_paths(),
        "self_modify": {"enabled": self_modify_enabled(), "frozen": is_frozen()},
    }
