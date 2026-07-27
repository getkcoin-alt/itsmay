"""Live self-context. Renders 'who I am right now' for every turn's system prompt.

Two surfaces:
- `render_self_context` — the compact, per-turn snapshot injected into the prompt
  (kept cheap; no subprocess / DB work on the hot path).
- `gather_inventory` — a fuller, *real* introspection of Scrappy's own state
  (code version, models, connectors, experts, memory, host, budget). Used by the
  identity endpoint and the first-boot "awakening" (`core/identity/bootstrap.py`),
  not per turn.
"""

from __future__ import annotations

import socket
import subprocess
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.config import get_settings

VERSION = "0.1.0"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def days_until(d: date) -> int:
    return (d - date.today()).days


def _active_model(s) -> str:  # noqa: ANN001 - Settings, avoids import cycle
    """The model actually serving this turn (provider-aware)."""
    return s.ollama_model if s.llm_provider.lower() == "ollama" else s.llm_model


@lru_cache(maxsize=1)
def git_sha() -> str | None:
    """Short commit SHA of the running code — how Scrappy knows *which* version of
    himself he is. Best-effort + cached (never on the per-turn path)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=_REPO_ROOT,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def version_str() -> str:
    sha = git_sha()
    return f"{VERSION}+{sha}" if sha else VERSION


def repo_root() -> Path:
    """Absolute path to Scrappy's own source tree (where his code lives)."""
    return _REPO_ROOT


async def gather_inventory(
    *, memory_count: int | None = None, budget: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A real snapshot of Scrappy's own state — what he can truthfully say about
    himself. Introspects settings, the connector/expert registries, and the memory
    backend; `memory_count` and `budget` (key-pool status) are passed by callers
    that hold the live handles.
    """
    from core.agents.experts import ALL_EXPERTS
    from core.connectors.registry import get_registry
    from core.memory.backend import describe_backend

    s = get_settings()
    reg = get_registry()
    installed = set(reg.connectors.keys())
    tool_count = sum(len(m.tools) for m in reg.manifests())
    experts = sorted(
        sp.tool_name for sp in ALL_EXPERTS
        if all(ns in installed for ns in sp.tool_namespaces)
    )
    mem = describe_backend()

    keys = (budget or {}).get("keys") or []
    return {
        "agent": "Scrappy Singh",
        "operator": "Karnveer Singh",
        "version": version_str(),
        "host": socket.gethostname(),
        "provider": s.llm_provider,
        "model": _active_model(s),
        "agent_model": s.llm_agent_model,
        "connectors": sorted(installed),
        "tool_count": tool_count,
        "experts": experts,
        "memory_backend": mem.get("backend"),
        "memory_location": mem.get("location"),
        "memory_count": memory_count,
        "key_pool": {
            "size": len(keys),
            "active": sum(1 for k in keys if k.get("active")),
        },
        "mission": s.mission_statement,
        "mission_target": s.mission_target_date.isoformat(),
    }


async def render_self_context(
    experts: list[str] | None = None, *, memory_count: int | None = None
) -> str:
    """Compact, model-readable snapshot of the agent's current state.

    `experts` lists the delegation tools currently available (e.g.
    'ask_memory_keeper'), so Scrappy knows which specialists he can hand off to.
    `memory_count` (when the caller has it) lets him speak to how much he remembers.
    """
    s = get_settings()
    target = s.mission_target_date
    days_left = days_until(target)

    lines = [
        f"Agent: Scrappy Singh  |  Version: {version_str()}  |  Model: {_active_model(s)}",
        "Operator: Karnveer Singh (handle: karnveer). Spell his name exactly: K-A-R-N-V-E-E-R.",
        f"Today: {date.today().isoformat()}",
        f"Mission: {s.mission_statement}",
        f"Target date: {target.isoformat()}  ({days_left} days remaining)",
        "Active capabilities: chat, episodic memory, semantic recall, long-term "
        "memory tools (save/recall), expert delegation, Mac control (voice).",
    ]
    if memory_count is not None:
        lines.append(f"Long-term memories stored: {memory_count}.")
    if experts:
        lines.append(
            "Experts you can delegate to via tool call: " + ", ".join(sorted(experts)) + "."
        )
    lines.append("Not yet wired: browser, Google connectors, nightly memory consolidation.")
    return "\n".join(lines)
