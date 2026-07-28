"""Restart signal — a durable marker that a self-change wants Scrappy restarted.

`apply_change` merges the new code into main, but the running process still holds
the OLD code until it restarts. It drops a marker here; the `scrappy up`
supervisor sees it on the next boot ("resumed after a self-update") and a future
watchdog can act on it (re-exec + health-probe + rollback-on-failure). Durable on
disk so it survives the very restart it requests.
"""

from __future__ import annotations

from pathlib import Path

from core.logging import get_logger

log = get_logger(__name__)


def _marker() -> Path:
    return Path.home() / ".itsmay" / "restart_requested"


def request_restart(*, reason: str = "") -> Path:
    """Signal that a restart is needed (durable). Returns the marker path."""
    m = _marker()
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text((reason or "restart").strip() + "\n")
    log.info("restart.requested", reason=reason[:120])
    return m


def pending_restart() -> str | None:
    """The reason a restart is pending, or None. (Empty file → 'restart'.)"""
    m = _marker()
    if not m.exists():
        return None
    try:
        return m.read_text().strip() or "restart"
    except OSError:
        return "restart"


def clear_restart() -> None:
    _marker().unlink(missing_ok=True)
