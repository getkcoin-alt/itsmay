"""IM-5.1b — self.propose_change (Claude Code implements on a branch; server guards).

Offline: the worker bridge is faked (returns a canned Claude Code report); the
guard is the real one. Nothing is ever merged.
"""

from __future__ import annotations

import json

from core.connectors.registry import get_registry
from core.identity import propose


class FakeBridge:
    def __init__(self, output: str = "", online: bool = True) -> None:
        self._out = output
        self._online = online
        self.calls: list[dict] = []

    def worker_online(self) -> bool:
        return self._online

    async def submit(self, *, agent_id, kind, cmd, timeout, task="", workdir=""):  # noqa: ASYNC109, E501
        self.calls.append({"agent_id": agent_id, "kind": kind, "cmd": cmd, "timeout": timeout})
        return self._out


def _report(files, tests_passed=True, branch="scrappy/self-add-uptime-endpoint") -> str:
    return json.dumps(
        {
            "branch": branch,
            "files_changed": files,
            "tests_passed": tests_passed,
            "test_summary": "251 passed",
            "notes": "did the thing",
        }
    )


# ── pure units ────────────────────────────────────────────────────────


def test_slugify():
    assert propose.slugify("Add a /v1/uptime endpoint") == "add-a-v1-uptime-endpoint"
    assert propose.slugify("!!!") == "change"
    assert len(propose.slugify("word " * 30)) <= 40


def test_build_prompt_embeds_guardrails_and_forbids_merge():
    p = propose.build_prompt("do X", "scrappy/self-do-x")
    assert "scrappy/self-do-x" in p
    assert "do X" in p
    assert "core/util/keypool.py" in p  # protected list is embedded
    assert "do NOT" in p and "merge" in p
    assert "files_changed" in p  # report format instructed


def test_extract_report_variants():
    assert propose.extract_report('noise\n{"branch":"b","files_changed":[]}\n') == {
        "branch": "b",
        "files_changed": [],
    }
    assert propose.extract_report("no json at all") is None
    assert propose.extract_report('{"no_branch": 1}') is None
    # Prefers the trailing report line over earlier brace noise.
    out = 'blah {not json} more\n{"branch":"x","tests_passed":true}'
    assert propose.extract_report(out)["branch"] == "x"


# ── orchestration (bridge faked, guard real) ──────────────────────────


async def test_happy_path_proposes_and_passes_guard(monkeypatch):
    monkeypatch.setattr(propose, "self_modify_enabled", lambda: True)
    bridge = FakeBridge(
        output="Working...\n" + _report(["apps/api/routers/uptime.py", "tests/test_uptime.py"])
    )
    p = await propose.propose_change("Add uptime endpoint", bridge=bridge)

    assert p.ok is True
    assert p.branch == "scrappy/self-add-uptime-endpoint"
    assert p.guard["allowed"] is True
    assert p.tests_passed is True
    assert "approve to apply" in p.summary
    # Dispatched to Claude Code with the computed branch in the prompt.
    assert bridge.calls and bridge.calls[0]["kind"] == "claude"
    assert "scrappy/self-add-uptime-endpoint" in bridge.calls[0]["cmd"]


async def test_guard_blocks_protected_file(monkeypatch):
    monkeypatch.setattr(propose, "self_modify_enabled", lambda: True)
    bridge = FakeBridge(output=_report(["core/util/keypool.py"]))
    p = await propose.propose_change("tamper with the keypool", bridge=bridge)

    assert p.ok is False
    assert p.guard["allowed"] is False
    assert "guardrails BLOCK" in p.summary


async def test_failing_tests_block_proposal(monkeypatch):
    monkeypatch.setattr(propose, "self_modify_enabled", lambda: True)
    bridge = FakeBridge(output=_report(["core/foo.py"], tests_passed=False))
    p = await propose.propose_change("do a thing", bridge=bridge)

    assert p.ok is False
    assert "tests did NOT pass" in p.summary


async def test_no_files_is_not_applicable(monkeypatch):
    monkeypatch.setattr(propose, "self_modify_enabled", lambda: True)
    p = await propose.propose_change("noop", bridge=FakeBridge(output=_report([])))
    assert p.ok is False  # empty diff → guard refuses "no changed files"


async def test_refuses_when_frozen(monkeypatch):
    monkeypatch.setattr(propose, "self_modify_enabled", lambda: False)
    bridge = FakeBridge(output=_report(["x.py"]))
    p = await propose.propose_change("anything", bridge=bridge)

    assert p.ok is False
    assert "disabled or frozen" in p.summary
    assert bridge.calls == []  # never dispatched Claude Code


async def test_refuses_without_worker(monkeypatch):
    monkeypatch.setattr(propose, "self_modify_enabled", lambda: True)
    bridge = FakeBridge(online=False)
    p = await propose.propose_change("anything", bridge=bridge)

    assert p.ok is False
    assert "scrappy worker" in p.summary
    assert bridge.calls == []


async def test_unparseable_report(monkeypatch):
    monkeypatch.setattr(propose, "self_modify_enabled", lambda: True)
    bridge = FakeBridge(output="I changed some files but forgot the JSON format")
    p = await propose.propose_change("do it", bridge=bridge)

    assert p.ok is False
    assert "couldn't parse" in p.summary
    assert p.raw


async def test_empty_goal():
    p = await propose.propose_change("   ", bridge=FakeBridge())
    assert p.ok is False and "empty goal" in p.summary


# ── connector wiring ──────────────────────────────────────────────────


def test_propose_tool_registered_not_approval_gated():
    tool = get_registry().get_tool("self.propose_change")
    assert tool is not None
    assert tool.spec.executor == "server"
    # Proposing is branch-only + freeze-gated; the APPLY step is what needs approval.
    assert tool.spec.requires_approval is False
    assert "git.branch" in tool.spec.side_effects
