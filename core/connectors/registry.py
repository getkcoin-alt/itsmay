"""Auto-discovering connector registry.

Walks `core/connectors/*/connector.py` (and `connectors_plugins/*/connector.py`
if it exists) and registers every subclass of `Connector` it finds. The
registry produces the OpenAI-compatible `tools=[...]` payload for chat
completions and dispatches tool calls to the right connector.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from dataclasses import dataclass, field

from core.connectors.base import (
    Connector,
    ConnectorManifest,
    InvocationContext,
    ToolSpec,
    compact_description,
)
from core.logging import get_logger

log = get_logger(__name__)


def flatten_result(res: object) -> str:
    """Flatten a connector `invoke()` result ({ok, result|error}) into a string
    the model can read as a tool result."""
    if isinstance(res, dict):
        if res.get("ok") is False:
            return f"error: {res.get('error', 'unknown error')}"
        if "result" in res:
            inner = res["result"]
            return inner if isinstance(inner, str) else _json(inner)
        return _json(res)
    return res if isinstance(res, str) else _json(res)


def _json(obj: object) -> str:
    try:
        return json.dumps(obj, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(obj)


@dataclass(slots=True)
class _RegisteredTool:
    connector: Connector
    spec: ToolSpec
    qualified_name: str  # "<connector>.<tool>"


@dataclass(slots=True)
class Registry:
    connectors: dict[str, Connector] = field(default_factory=dict)
    _tools_by_qname: dict[str, _RegisteredTool] = field(default_factory=dict)

    def register(self, conn: Connector) -> None:
        name = conn.manifest.name
        self.connectors[name] = conn
        for spec in conn.manifest.tools:
            qname = f"{name}.{spec.name}"
            self._tools_by_qname[qname] = _RegisteredTool(conn, spec, qname)
        log.info(
            "registry.connector_registered",
            connector=name,
            tools=[t.name for t in conn.manifest.tools],
        )

    def manifests(self) -> list[ConnectorManifest]:
        return [c.manifest for c in self.connectors.values()]

    def tools_openai(
        self, *, executors: set[str] | None = None, compact: bool = False
    ) -> list[dict]:
        """Return the OpenAI/Groq-compatible `tools=[...]` payload.

        Pass `executors` (e.g. {"server"} or {"client_mac"}) to include only
        tools that run on those executors. Default: every tool. `compact=True`
        trims verbose descriptions for the LLM payload (#16) — used at the model
        call sites; the console/status views keep the full manifest descriptions.
        """
        out: list[dict] = []
        for rt in self._tools_by_qname.values():
            if executors is not None and rt.spec.executor not in executors:
                continue
            desc = compact_description(rt.spec.description) if compact else rt.spec.description
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": rt.qualified_name,
                        "description": desc,
                        "parameters": rt.spec.parameters,
                    },
                }
            )
        return out

    def executor_for(self, qualified_name: str) -> str | None:
        rt = self._tools_by_qname.get(qualified_name)
        return rt.spec.executor if rt else None

    def get_tool(self, qualified_name: str) -> _RegisteredTool | None:
        return self._tools_by_qname.get(qualified_name)

    async def invoke(
        self, qualified_name: str, args: dict, ctx: InvocationContext
    ) -> dict:
        """Server-side tool dispatch. Returns `{ok, result|error}`."""
        rt = self._tools_by_qname.get(qualified_name)
        if rt is None:
            return {"ok": False, "error": f"unknown tool: {qualified_name}"}
        if rt.spec.executor != "server":
            return {
                "ok": False,
                "error": f"{qualified_name} runs on the {rt.spec.executor} client, not the server",
            }
        # Server-side approval gate (defense in depth: every execution path —
        # orchestrator, expert sub-agents — funnels through here). A tool marked
        # `requires_approval` is refused unless the operator named it in this
        # request's `approved_tools`; the flag is otherwise advisory-only.
        if rt.spec.requires_approval and qualified_name not in ctx.approved_tools:
            log.warning("registry.approval_blocked", tool=qualified_name)
            effects = ", ".join(rt.spec.side_effects) or "side effects"
            return {
                "ok": False,
                "error": (
                    f"approval_required: {qualified_name} ({effects}) was NOT executed — "
                    "it needs the user's explicit approval. Describe the exact action "
                    "and ask the user to confirm."
                ),
            }
        try:
            result = await rt.connector.invoke(rt.spec.name, args, ctx)
            return {"ok": True, "result": result}
        except Exception as e:
            log.exception("registry.tool_failed", tool=qualified_name)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


_registry: Registry | None = None


def get_registry() -> Registry:
    """Idempotent. Discovers connectors on first call."""
    global _registry
    if _registry is not None:
        return _registry
    reg = Registry()
    _discover_into(reg, "core.connectors")
    try:
        _discover_into(reg, "connectors_plugins")
    except ModuleNotFoundError:
        pass  # optional drop-in dir
    _registry = reg
    log.info("registry.ready", connector_count=len(reg.connectors))
    return reg


def _discover_into(reg: Registry, package_name: str) -> None:
    """Find every `<package>.<sub>.connector` module and register Connector
    subclasses defined there."""
    package = importlib.import_module(package_name)
    if not hasattr(package, "__path__"):
        return
    for _finder, modname, ispkg in pkgutil.iter_modules(package.__path__):
        if not ispkg:
            continue
        try:
            mod = importlib.import_module(f"{package_name}.{modname}.connector")
        except ModuleNotFoundError:
            continue
        for attr in vars(mod).values():
            if (
                isinstance(attr, type)
                and attr is not Connector
                and issubclass(attr, Connector)
                and getattr(attr, "manifest", None) is not None
            ):
                try:
                    reg.register(attr())
                except Exception as e:
                    log.exception(
                        "registry.connector_failed", module=modname, err=str(e)
                    )
