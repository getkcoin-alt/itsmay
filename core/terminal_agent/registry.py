"""Global in-memory registry for TerminalAgent instances."""

from __future__ import annotations

from typing import Any

from core.brain.llm import LLMClient
from core.terminal_agent.agent import TerminalAgent


class AgentStore:
    _MAX = 100

    def __init__(self) -> None:
        self._agents: dict[str, TerminalAgent] = {}

    def spawn(
        self,
        task: str,
        llm: LLMClient,
        *,
        embedder: Any | None = None,
        semantic: Any | None = None,
        user_uuid: Any | None = None,
    ) -> TerminalAgent:
        agent = TerminalAgent(
            task, embedder=embedder, semantic=semantic, user_uuid=user_uuid
        )
        self._agents[agent.id] = agent
        agent.launch(llm)
        if len(self._agents) > self._MAX:
            oldest = next(iter(self._agents))
            del self._agents[oldest]
        return agent

    def get(self, agent_id: str) -> TerminalAgent | None:
        return self._agents.get(agent_id)

    def list_all(self) -> list[TerminalAgent]:
        return list(reversed(list(self._agents.values())))


_store: AgentStore | None = None


def get_agent_store() -> AgentStore:
    global _store
    if _store is None:
        _store = AgentStore()
    return _store
