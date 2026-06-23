"""Sub-agent definitions.

A *sub-agent* is one of Scrappy's experts: a specialist with its own persona,
its own slice of tools, and its own multi-step reasoning loop. Scrappy (the
top-level orchestrator) delegates a self-contained task to an expert and gets a
synthesized answer back — he doesn't need to know how the expert does its job.

A `SubAgentSpec` is pure data: name, persona, which connector namespaces it may
use, and loop limits. The runner (`core.agents.runner`) turns a spec + a task
into an actual agentic loop. The registry (`core.agents.registry`) decides which
experts are *available* (an expert is only offered to Scrappy once every
connector it depends on is installed).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SubAgentSpec:
    """A registered expert. Immutable — define these once at module load."""

    name: str  # machine name, e.g. "memory_keeper" → tool `ask_memory_keeper`
    title: str  # human title, e.g. "Memory Keeper"
    expertise: str  # one line; becomes the delegation tool's description
    system_prompt: str  # the expert's persona + operating instructions
    # Connector namespaces this expert may call (e.g. ("memory",)). Empty tuple
    # means a pure-reasoning expert with no tools. The expert is only available
    # when *all* of these namespaces are registered.
    tool_namespaces: tuple[str, ...] = field(default_factory=tuple)
    model: str | None = None  # optional model override (else the default LLM)
    temperature: float = 0.3  # experts run cooler than conversational Scrappy
    max_iters: int = 6  # tool-loop cap inside the expert

    @property
    def tool_name(self) -> str:
        """The function name Scrappy calls to delegate to this expert."""
        return f"ask_{self.name}"


@dataclass(slots=True)
class SubAgentResult:
    """What a delegated expert hands back to Scrappy."""

    agent: str
    text: str
    iterations: int = 0
    tools_used: list[str] = field(default_factory=list)
    stop_reason: str = "stop"
