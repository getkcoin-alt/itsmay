"""IM-5.1a — self.describe (read-only introspection) + connector registration.

describe_self() reads the real repo (git + fs) — hermetic (this tree is a git
repo), no mocking.
"""

from __future__ import annotations

from core.connectors.base import InvocationContext
from core.connectors.registry import get_registry
from core.connectors.self_mod.connector import SelfConnector
from core.identity.introspect import describe_self


def test_describe_self_reports_real_repo_state():
    d = describe_self()
    assert d["version"]
    assert isinstance(d["branch"], str) and d["branch"]
    assert isinstance(d["recent_commits"], list)
    assert isinstance(d["clean"], bool)
    assert isinstance(d["dirty_files"], list)
    # Protected paths surfaced for transparency (guard is the source of truth).
    assert "core/util/keypool.py" in d["protected_paths"]
    assert {"core", "apps", "tests"} <= set(d["top_level"])
    assert set(d["self_modify"]) == {"enabled", "frozen"}


async def test_connector_describe_returns_snapshot():
    conn = SelfConnector()
    assert conn.manifest.name == "self"
    spec = conn.manifest.tools[0]
    assert spec.name == "describe"
    assert spec.executor == "server"
    assert spec.requires_approval is False  # read-only: no approval

    out = await conn.invoke("describe", {}, InvocationContext())
    assert isinstance(out, dict) and out["version"]


async def test_connector_rejects_unknown_action():
    out = await SelfConnector().invoke("nope", {}, InvocationContext())
    assert isinstance(out, str) and out.startswith("error:")


def test_self_connector_is_discovered():
    reg = get_registry()
    assert "self" in reg.connectors
    tool = reg.get_tool("self.describe")
    assert tool is not None
    assert tool.spec.requires_approval is False
