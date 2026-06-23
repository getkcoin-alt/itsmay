"""Connector base classes.

A `Connector` is a self-contained capability that exposes one or more `ToolSpec`s
to the LLM. Connectors live under `core/connectors/<name>/` (or under
`connectors_plugins/<name>/` for drop-ins). Each connector declares:

- A `ConnectorManifest` (name, version, list of tools)
- An async `invoke(action, args, ctx)` method for tools the *server* executes
- Tools marked `executor="client_mac"` (or other client executors) are routed
  to the client over SSE; the server does not call them.

The connector framework intentionally has no opinion on what side runs the
tool — it just describes them to the LLM and gives the routing layer a clean
hook to dispatch the result. See `core/connectors/registry.py` for discovery
and `apps/api/routers/chat.py` for the dispatch glue.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

# Where the tool actually runs. Right now we support:
#   - "server"     — invoked inside the FastAPI process
#   - "client_mac" — emitted as an SSE event for the Mac voice agent to execute
Executor = Literal["server", "client_mac"]


class ToolSpec(BaseModel):
    """A single LLM-callable tool. `parameters` is JSON Schema (OpenAI tools format)."""

    name: str = Field(..., description="Bare tool name (no connector prefix).")
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    executor: Executor = "server"
    requires_approval: bool = False
    side_effects: list[str] = Field(default_factory=list)


class ConnectorManifest(BaseModel):
    name: str = Field(..., description="Connector namespace; used as 'name.tool' for routing.")
    version: str = "0.1.0"
    description: str = ""
    tools: list[ToolSpec] = Field(default_factory=list)


@dataclass(slots=True)
class InvocationContext:
    """Passed to `Connector.invoke()` for server-executed tools."""

    user_id: str | None = None
    session_id: str | None = None
    voice_mode: bool = False


class Connector(ABC):
    """Base class. Subclasses set `manifest` and implement `invoke` for any
    server-side tools they own."""

    manifest: ConnectorManifest

    @abstractmethod
    async def invoke(self, action: str, args: dict, ctx: InvocationContext) -> Any:
        """Execute a tool by bare name (without the connector prefix).
        Connectors whose tools are all client-side may raise NotImplementedError."""
        ...

    async def health(self) -> dict:
        return {"name": self.manifest.name, "ok": True}
