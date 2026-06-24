# Terminal Agents — How to Use Scrappy's Workers

## The idea

You talk to Scrappy in plain English. Scrappy decides when a task needs a
worker, spawns one (or several), manages them, and reports back to you.
You never touch the agents directly — Scrappy is the manager, agents are
the workers.

```
You ──► Scrappy ──► terminal.spawn()  ──► Agent A (bash loop)
                ├──► terminal.spawn()  ──► Agent B (bash loop)
                └──► terminal.status() ◄── results come back
                          │
                          └──► Scrappy summarises and replies to you
```

---

## Quick-start: what to say to Scrappy

These are real messages you can send in the Chat tab.

### Single task

```
Write me a Python script that fetches the top 10 Hacker News stories and
saves them to a JSON file. Run it and show me the output.
```

Scrappy will:
1. Call `terminal.spawn` with a precise task description
2. Call `terminal.status` every few seconds until done
3. Read the result and reply to you with a summary

### Parallel workers

```
I need three things done at the same time:
1. Write a Fibonacci generator in Python and benchmark it
2. Create a shell script that checks system disk usage and emails a warning if over 80%
3. Write a Node.js Express hello-world server with a /health endpoint

Spawn a separate agent for each.
```

Scrappy spawns all three simultaneously, polls them, and stitches the
results into one reply.

### Multi-step pipeline

```
Build me a web scraper for Hacker News:
- Scrape the front page
- Store results in a SQLite database
- Write a query that returns the top 5 stories by score
Run all three steps end to end and show me the final query result.
```

Scrappy can chain agent outputs — the result of one becomes input to the next.

### Iterative refinement

```
Write a REST API in Python (FastAPI) with CRUD endpoints for a "task" model.
Run it, then run tests against it, then fix any failures.
```

Scrappy reads the test failures from the first agent and spawns a second
agent with a "fix these specific errors" task.

---

## What agents can do (bash access)

| Category | Examples |
|---|---|
| Write code | Python, JS, Shell, SQL, any language |
| Run code | `python script.py`, `node app.js`, `pytest` |
| Install packages | `pip install`, `npm install`, `apt-get` |
| File operations | create, read, edit, move, zip files |
| Git operations | clone, commit, diff, log |
| Network | `curl`, `wget`, API calls from the shell |
| Data processing | parse CSV/JSON, run SQL queries |
| System info | disk, memory, process list, environment |

Each agent gets its own isolated working directory under `/tmp/scrappy-agent-<id>/`.

---

## Watching agents live — the Agents tab

Click the **Agents** tab in the console at any time to see what's running.

| Status | Meaning |
|---|---|
| `pending` | Spawned, waiting to start |
| `running` | Active — model is thinking or running commands |
| `done` | Finished successfully |
| `error` | Crashed or hit the turn limit |

Click any agent card to expand it. You'll see the full log:

- **thought** — what the model reasoned before each command
- **cmd** — the exact shell command it ran
- **output** — stdout + stderr from that command
- **result** — final summary when done
- **error** — what went wrong if it failed

The Agents tab auto-refreshes every 3 seconds while workers are running.

---

## Prompting Scrappy effectively

Scrappy handles the delegation — you just need to be clear about the goal.

**Good prompts:**
- "Build X and run it" — Scrappy knows to spawn a worker
- "Do these 3 things in parallel" — Scrappy spawns 3 agents
- "Try it, fix any errors, run it again" — Scrappy iterates

**Scrappy will automatically decide to use agents when:**
- The task needs to execute code
- The task involves files or the filesystem
- The task is long-running (would be rude to block your chat)
- You ask for parallel work

**You don't need to say "spawn an agent" — just describe the outcome you want.**

---

## Example conversation

```
You:     Build a Python CLI tool that converts CSV to JSON.
         Accept filename as argument. Run it on a sample file.

Scrappy: Spawning a terminal agent for this…

         [agent a1b2c3d4 · running]
           cmd    python3 -c "import csv, json, sys…"
           cmd    echo "name,age\nAlice,30\nBob,25" > sample.csv
           cmd    python3 csv_to_json.py sample.csv
           output [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]
           result Script written to csv_to_json.py. Tested on sample.csv —
                  outputs valid JSON array. Usage: python3 csv_to_json.py <file>.

         Done. The agent wrote csv_to_json.py in its working directory
         (/tmp/scrappy-agent-a1b2c3d4/). Want me to show you the source,
         or copy it somewhere?
```

---

## Limits and quirks

- **Agents run on the server**, not your Mac. Mac-only tools (Chrome, notifications)
  are only available in the voice loop, not inside agents.
- **Working directory** is ephemeral (`/tmp/scrappy-agent-<id>/`). Files don't
  persist across server restarts. Ask the agent to print outputs or Scrappy to
  relay content if you want to keep something.
- **Max 20 turns** per agent (prevents infinite loops). Complex tasks may need
  Scrappy to re-spawn with a more focused task if an agent hits the limit.
- **Network access** depends on what's available server-side (Railway by default).
- **Up to 100 agents** are kept in memory. Oldest are evicted when the cap is hit.

---

## Architecture (for the curious)

```
TerminalAgent._run()
  └─ run_tool_loop(llm, messages, _BashRouter)
       ├─ Token events  →  log["thought"]
       ├─ ToolResult    →  flush thought, log["cmd"] + log["output"] already captured
       └─ Done          →  log["result"], status = "done"

TerminalConnector (auto-discovered, server-side)
  ├─ terminal.spawn(task)   → AgentStore.spawn() → TerminalAgent.launch()
  ├─ terminal.status(id)    → TerminalAgent.to_dict(full=True)
  └─ terminal.list()        → AgentStore.list_all()

GET /v1/agents        → frontend Agents tab list
GET /v1/agents/{id}   → frontend expanded log view
```
