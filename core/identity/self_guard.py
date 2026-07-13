"""Guardrails for self-modification — the rules that keep a self-editing Scrappy *yours*.

This is the safety floor Epic 5 sits on. Before any self-change is applied, the
proposed set of changed files is run through `check_change`, which refuses:

- **security-critical files** — the auth gate, the approval-enforcement path, the
  key pool, the config, and *this guard itself* (so he can't disable his own
  guardrails);
- **out-of-scope paths** — absolute paths, `..` traversal, git internals, CI, and
  secret/credential files;
- **everything, when self-modification is disabled or frozen** (`SELF_MODIFY=off`,
  or the `scrappy freeze` kill switch).

Pure policy: no I/O beyond checking the freeze marker, no dependency on the
`self.*` connector (which doesn't exist yet). IM-5.1 will call `check_change`
before it ever merges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from core.config import get_settings
from core.logging import get_logger

log = get_logger(__name__)

# Exact repo-relative paths self-modification may NEVER touch. These are the
# invariants that make the whole safety model work — auth (#12), the approval
# gate (#13), the key pool (#14), config, and the guard itself.
PROTECTED_FILES: frozenset[str] = frozenset(
    {
        "apps/api/middleware/auth.py",       # fail-closed auth gate (#12)
        "core/brain/agent_loop.py",          # approval enforcement in the loop (#13)
        "core/brain/orchestrator.py",        # approval enforcement (#13)
        "core/connectors/registry.py",       # approval choke point (#13)
        "core/util/keypool.py",              # key-pool integrity (#14)
        "core/config.py",                    # settings incl. secret defaults
        "core/identity/self_guard.py",       # the guard cannot edit its own guard
        "pyproject.toml",                    # deps / build — boot-critical
        "railway.toml",                      # deploy config
        "Dockerfile",                        # runtime image
    }
)
# Whole subtrees that are off-limits.
_PROTECTED_PREFIXES: tuple[str, ...] = (".git/", ".github/")
_PROTECTED_EXACT: frozenset[str] = frozenset({".git", ".github"})
# Secret / credential files, matched by name or suffix anywhere in the tree.
_SECRET_NAMES: frozenset[str] = frozenset({".env", "config.env"})
_SECRET_SUFFIXES: tuple[str, ...] = (".pem", ".key", ".pfx", ".p12")


def protected_paths() -> list[str]:
    """The protected set, for transparency (a future `self.describe` can show it)."""
    return sorted(PROTECTED_FILES) + [f"{p}**" for p in _PROTECTED_PREFIXES]


def _why_protected(path: str) -> str | None:
    """Reason this path is off-limits to self-modification, or None if it's fair game."""
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        return "empty path"
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        return "absolute path (outside the repo)"
    parts = PurePosixPath(raw).parts
    if ".." in parts:
        return "path traversal ('..')"
    norm = PurePosixPath(raw).as_posix()
    if norm in PROTECTED_FILES:
        return "security-critical file (auth / approval / keypool / config / guard)"
    if norm in _PROTECTED_EXACT or norm.startswith(_PROTECTED_PREFIXES):
        return "git / CI internals"
    name = parts[-1]
    if name in _SECRET_NAMES or name.endswith(_SECRET_SUFFIXES):
        return "secret / credential file"
    return None


def _freeze_marker() -> Path:
    """The kill-switch marker. Its existence freezes self-modification at runtime
    (no restart needed). Written by `scrappy freeze`, removed by `scrappy unfreeze`."""
    return Path.home() / ".itsmay" / "self_modify.frozen"


def is_frozen() -> bool:
    return _freeze_marker().exists()


def freeze() -> Path:
    """Engage the kill switch (create the marker). Returns its path."""
    m = _freeze_marker()
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text("frozen\n")
    log.warning("self_guard.frozen", marker=str(m))
    return m


def unfreeze() -> bool:
    """Release the kill switch (remove the marker). Returns True if one existed."""
    m = _freeze_marker()
    existed = m.exists()
    m.unlink(missing_ok=True)
    if existed:
        log.info("self_guard.unfrozen")
    return existed


def self_modify_enabled() -> bool:
    """Master switch: on unless `SELF_MODIFY=off` in config or a freeze marker is set."""
    if not get_settings().self_modify:
        return False
    return not is_frozen()


@dataclass(slots=True)
class GuardVerdict:
    allowed: bool
    reason: str
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (path, why)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "rejected": [{"path": p, "why": w} for p, w in self.rejected],
        }


def check_change(paths: list[str], *, enabled: bool | None = None) -> GuardVerdict:
    """Decide whether a self-change touching `paths` may be applied.

    `enabled` overrides the live switch (for callers/tests that compute it
    separately); by default it reads `self_modify_enabled()`. Fail-closed: a
    change is allowed only if self-modification is on AND every path is in scope.
    """
    on = self_modify_enabled() if enabled is None else enabled
    if not on:
        return GuardVerdict(False, "self-modification is disabled or frozen")
    if not paths:
        return GuardVerdict(False, "no changed files to apply")

    rejected = [(p, why) for p in paths if (why := _why_protected(p)) is not None]
    if rejected:
        log.warning("self_guard.rejected", count=len(rejected), paths=[p for p, _ in rejected])
        return GuardVerdict(
            False, f"{len(rejected)} path(s) are protected from self-modification", rejected
        )
    return GuardVerdict(True, "all changed paths are in scope")
