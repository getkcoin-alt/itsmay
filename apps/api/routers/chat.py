"""POST /v1/chat — streaming chat with episodic + semantic memory."""

from __future__ import annotations

import json
import time
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from apps.api.deps import get_embedder, get_episodic, get_llm, get_semantic
from core.agents.registry import get_agent_registry
from core.brain.agent_loop import (
    ApprovalRequired,
    ClientToolCall,
    Done,
    Token,
    ToolProgress,
    ToolResult,
    ToolStart,
    run_tool_loop,
)
from core.brain.context_builder import build_messages
from core.brain.llm import LLMClient
from core.brain.orchestrator import Orchestrator
from core.config import get_settings
from core.connectors.base import InvocationContext
from core.connectors.registry import get_registry
from core.identity.self_model import render_self_context
from core.logging import get_logger
from core.memory.embedder import Embedder
from core.memory.episodic import EpisodicStore
from core.memory.semantic import SemanticStore

log = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["chat"])

# Fact-like memory kinds for the "RELEVANT MEMORIES" block. Procedural memories
# (playbooks) are retrieved separately into their own "## PLAYBOOKS" block, so
# they don't double-appear here.
_FACT_KINDS = {"factual", "semantic", "reflection", "episodic"}


def _classify_chat_error(exc: Exception, llm: LLMClient) -> dict:
    """Turn a raw stream exception into a structured error event with a calm,
    *speakable* message — so the voice client can say it instead of dumping a
    stack trace. Rate-limit messages are tailored to the live key-pool state."""
    msg = str(exc)
    low = msg.lower()
    payload: dict = {"error": msg}

    rate_limited = ("rate limit" in low) or ("429" in low) or ("unavailable after" in low)
    if rate_limited:
        payload["kind"] = "rate_limit"
        try:
            keys = llm.keys.status().get("keys", [])
            total = len(keys)
            active = sum(1 for k in keys if k.get("active"))
        except Exception:
            total, active = 0, 0
        if total <= 1:
            payload["speak"] = (
                "I'm rate-limited right now — my key is tapped out. Add a couple "
                "more keys to LLM_API_KEY, or give it a little time."
            )
        elif active == 0:
            payload["speak"] = (
                f"All {total} of my keys are rate-limited right now — they reset on "
                "Groq's daily cycle. Add another key, or give it a bit."
            )
        else:
            payload["speak"] = (
                "I hit a rate limit but I'm rotating to another key — give me a second."
            )
    else:
        payload["kind"] = "error"
        payload["speak"] = "Something glitched on my end — say that again?"
    return payload


def _silent_turn_fallback(tool_steps: list[dict], last_tool_summary: str) -> str:
    """What to say when the model produced no text at all this turn.

    Blank output reads as being ignored, which is worse than an imperfect answer.
    If a tool ran we relay what it came back with; otherwise we ask for a retry.
    """
    summary = " ".join((last_tool_summary or "").split())
    if tool_steps and summary:
        failed = summary.startswith(("error:", "blocked:"))
        tool = tool_steps[-1].get("tool", "that")
        if failed:
            return f"I tried {tool} and it didn't go through — {summary[:200]}"
        return f"Here's what {tool} came back with: {summary[:200]}"
    if tool_steps:
        return (
            f"I ran {tool_steps[-1].get('tool', 'a tool')} but got nothing useful back. "
            "Want me to try a different angle?"
        )
    return "I didn't get that one out — say it again?"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: UUID | None = None
    channel: str = "api"
    temperature: float = 0.7
    # Qualified tool names (e.g. "gmail.send") the user explicitly approved for
    # THIS turn. `requires_approval` tools are blocked server-side unless named
    # here; the `approval_required` SSE event tells the client what to confirm.
    approved_tools: list[str] = Field(default_factory=list)


@router.post("/chat")
async def chat(
    body: ChatRequest,
    llm: LLMClient = Depends(get_llm),
    embedder: Embedder = Depends(get_embedder),
    episodic: EpisodicStore = Depends(get_episodic),
    semantic: SemanticStore = Depends(get_semantic),
) -> EventSourceResponse:
    settings = get_settings()
    user_id = await episodic.get_or_create_user(settings.user_handle)

    if body.session_id is not None and await episodic.session_exists(
        body.session_id, user_id
    ):
        session_id = body.session_id
    else:
        # Either no session_id given, or a stale one (e.g. client cache from a
        # different deploy). Open a fresh one and the SSE 'session' event tells
        # the client the new id so it can update its cache.
        session_id = await episodic.open_session(user_id, channel=body.channel)

    # Embed the user turn (best-effort). If the embedder is down or rate-limited,
    # we just lose semantic recall + vector persistence for this turn — the
    # chat itself must not fail.
    user_embedding = None
    try:
        user_embedding = await embedder.embed(body.message)
    except Exception as e:
        log.warning("embed.user_failed", err=str(e))

    # Persist the user message (embedding may be None — that's fine).
    await episodic.append_message(
        session_id, "user", body.message, embedding=user_embedding
    )

    # Pull recent window + retrieved semantic memories.
    # Voice mode pulls fewer memories — every token shaved off prompt is felt
    # as silence by the user. Voice also takes a shorter conversation window.
    voice_mode = "voice" in (body.channel or "").lower()
    retrieval_k = 3 if voice_mode else settings.retrieval_k
    recent_n = 6 if voice_mode else settings.recent_message_window

    recent = await episodic.recent_window(session_id, recent_n)
    if user_embedding is not None:
        # A similarity floor keeps unrelated memories out of the prompt entirely —
        # padding context with noise makes answers worse, not better.
        min_sim = settings.retrieval_min_similarity
        retrieved = await semantic.search(
            user_id, user_embedding, k=retrieval_k,
            kinds=_FACT_KINDS, min_similarity=min_sim,
        )
        # A couple of relevant playbooks (procedural memory) for this goal.
        playbooks = await semantic.search(
            user_id, user_embedding, k=2,
            kinds={"procedural"}, min_similarity=min_sim,
        )
    else:
        retrieved = []
        playbooks = []
    experts = [spec.tool_name for spec in get_agent_registry().available]
    self_ctx = await render_self_context(experts=experts)

    # Drop the latest user message from recent (we add it back as the live turn).
    recent_for_prompt = [m for m in recent if not (m.role == "user" and m.content == body.message)]

    messages = build_messages(
        self_context=self_ctx,
        retrieved_memories=[m.content for m in retrieved],
        recent_messages=recent_for_prompt,
        user_input=body.message,
        voice_mode=voice_mode,
        playbooks=[m.content for m in playbooks],
    )

    async def event_stream():
        # Send session_id first so the client can pin the conversation.
        yield {
            "event": "session",
            "data": json.dumps({"session_id": str(session_id)}),
        }

        full: list[str] = []
        tool_steps: list[dict] = []  # this turn's tool calls → a workflow trace
        last_tool_summary = ""  # rescues a turn that ends without any text
        t0 = time.perf_counter()
        prompt_tokens = None
        completion_tokens = None

        # Scrappy's toolbox: expert sub-agents (ask_*) and in-process server
        # connectors (memory.*) are always available; Mac/client tools are added
        # only when a voice client is listening to execute them.
        connectors = get_registry()
        orchestrator = Orchestrator(connectors, get_agent_registry(), voice_mode=voice_mode)
        ctx = InvocationContext(
            user_id=settings.user_handle,
            user_uuid=user_id,
            session_id=str(session_id),
            voice_mode=voice_mode,
            approved_tools=frozenset(body.approved_tools),
            llm=llm,
            registry=connectors,
            embedder=embedder,
            semantic=semantic,
        )

        # Multi-turn agentic loop: streams tokens, executes server tools and
        # delegated experts, feeds results back, and forwards Mac tool calls to
        # the client. Runs until the model answers without calling a tool.
        try:
            async for ev in run_tool_loop(
                llm=llm,
                messages=messages,
                router=orchestrator,
                ctx=ctx,
                temperature=body.temperature,
                max_iters=settings.tool_loop_max_iters,
            ):
                if isinstance(ev, Token):
                    full.append(ev.text)
                    yield {"event": "token", "data": ev.text}
                elif isinstance(ev, ClientToolCall):
                    yield {
                        "event": "tool_call",
                        "data": json.dumps(
                            {
                                "id": ev.id,
                                "name": ev.name,
                                "arguments": ev.arguments,
                                "executor": "client_mac",
                            }
                        ),
                    }
                    tool_steps.append({"tool": ev.name, "ok": True})
                elif isinstance(ev, ToolStart):
                    yield {
                        "event": "tool_start",
                        "data": json.dumps({"id": ev.id, "tool": ev.name}),
                    }
                elif isinstance(ev, ToolProgress):
                    # A slow tool narrating mid-run (e.g. a Claude Code build).
                    # Surfaced live so the voice client can speak the play-by-play.
                    yield {
                        "event": "tool_progress",
                        "data": json.dumps({"tool": ev.name, "text": ev.text}),
                    }
                elif isinstance(ev, ApprovalRequired):
                    # Blocked server-side. The client shows a confirm prompt and
                    # re-sends the message with `approved_tools: [ev.name]`.
                    yield {
                        "event": "approval_required",
                        "data": json.dumps(
                            {"id": ev.id, "name": ev.name, "arguments": ev.arguments}
                        ),
                    }
                elif isinstance(ev, ToolResult):
                    yield {
                        "event": "status",
                        "data": json.dumps({"tool": ev.name, "result": ev.summary}),
                    }
                    tool_steps.append(
                        {"tool": ev.name, "ok": not ev.summary.startswith(("error:", "blocked:"))}
                    )
                    last_tool_summary = ev.summary
                elif isinstance(ev, Done):
                    prompt_tokens = ev.prompt_tokens
                    completion_tokens = ev.completion_tokens
                    break
        except Exception as e:
            log.exception("chat.stream_failed", err=str(e))
            yield {"event": "error", "data": json.dumps(_classify_chat_error(e, llm))}
            return

        latency_ms = int((time.perf_counter() - t0) * 1000)
        assistant_text = "".join(full).strip()

        # A turn must never end in silence. Models sometimes emit a tool call and
        # then no closing text at all, which reaches the user as a blank reply and
        # looks like Scrappy ignored them. Say something honest instead — leaning
        # on the last tool result when there is one.
        if not assistant_text:
            fallback = _silent_turn_fallback(tool_steps, last_tool_summary)
            assistant_text = fallback
            full.append(fallback)
            yield {"event": "token", "data": fallback}

        # Embed + persist the assistant turn. Failures here must not break the response.
        try:
            assistant_emb = await embedder.embed(assistant_text) if assistant_text else None
            await episodic.append_message(
                session_id,
                "assistant",
                assistant_text,
                embedding=assistant_emb,
                tokens_in=prompt_tokens,
                tokens_out=completion_tokens,
                latency_ms=latency_ms,
            )
        except Exception as e:
            log.warning("chat.persist_assistant_failed", err=str(e))

        # Record the workflow trace for this turn (only if tools ran) as a
        # role="tool" row — the raw material the nightly miner turns into
        # playbooks. Best-effort; never breaks the response.
        if tool_steps:
            try:
                trace = json.dumps({"goal": body.message[:200], "steps": tool_steps})
                await episodic.append_message(session_id, "tool", trace)
            except Exception as e:
                log.warning("chat.persist_trace_failed", err=str(e))

        yield {
            "event": "done",
            "data": json.dumps(
                {
                    "session_id": str(session_id),
                    "tokens_in": prompt_tokens,
                    "tokens_out": completion_tokens,
                    "latency_ms": latency_ms,
                }
            ),
        }

    return EventSourceResponse(event_stream())


@router.get("/health")
async def health(
    llm: LLMClient = Depends(get_llm),
    embedder: Embedder = Depends(get_embedder),
) -> dict:
    from core.memory.backend import describe_backend

    return {
        "api": "ok",
        "llm": await llm.health(),
        "embedder": await embedder.health(),
        "memory": describe_backend(),
    }
