# Scrappy's memory

Scrappy has three kinds of memory. Together they're the moat: the longer it runs
for you, the more it knows and the better it works.

| Type | What it holds | Where | Retrieved |
|---|---|---|---|
| **Episodic** | Turn-by-turn messages (the raw conversation) | `messages` table | recent window per session |
| **Semantic** | Durable *facts* — preferences, decisions, identities, goals | `memories` (kind `factual`/`semantic`/`reflection`) | vector similarity to the current turn |
| **Procedural** | *Playbooks* — how you've handled a task before | `memories` (kind `procedural`) | vector similarity, injected as a distinct block |

Backends: Postgres + pgvector in the cloud, or a single SQLite file in the
sovereign local path (`COMPANION_SQLITE_PATH` / `SQLITE_PATH`). All three memory
types work on both.

## Procedural memory (playbooks)

The newest layer — Scrappy's "connectome of your workflows." Instead of
re-reasoning a multi-step task from scratch every time, it learns the shape of
the workflows it actually runs and follows its own proven procedure.

**How it flows:**

1. **Capture** — every tool-using turn, the chat router records a compact trace
   (`role="tool"` message: `{"goal", "steps":[{"tool","ok"}]}`). Pure-chat turns
   write nothing. No migration — the `messages` table already allows `tool` rows,
   and they're excluded from the conversation window.
2. **Mine** — the nightly consolidation pass (`scrappy --consolidate` →
   `POST /v1/memory/consolidate`) runs `core/memory/procedural.py::mine_workflows`
   over recent traces, asks the model to name the *recurring* multi-step patterns,
   and saves each as a `procedural` memory — a **playbook** (trigger + ordered
   steps). Idempotent on content, like the fact consolidator.
3. **Inject** — on each turn, a small `search(..., kinds={"procedural"})` pulls the
   playbooks relevant to the current goal into a distinct
   **"## PLAYBOOKS — how you've handled this before"** block in the system prompt.

**Guidance, not autopilot.** Playbooks are injected as guidance Scrappy *follows*;
it does not blindly replay a tool sequence. Any step that touches a
`requires_approval` tool still goes through the approval gate (see the auth /
approval model). Auto-replay (behind approvals) is a deliberate follow-up.

**See them:** the web console's memory browser lists playbooks with a
`procedural` pill; `scrappy --consolidate` reports how many were mined.
