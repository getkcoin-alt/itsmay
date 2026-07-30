# Vault Zeta Node SSN-92C

Scrappy Singh — sovereign personal AI operator. Cloud-deployed FastAPI orchestrator on Railway, brain on Groq (Gemma + Whisper), embeddings on OpenAI, voice via ElevenLabs, push-to-talk client on the MacBook.

> The MacBook used to host everything and would lock up during LLM inference. The heavy compute now lives in the cloud; the laptop only runs the voice client.

---

## Topology

```
┌─────────────── MAC ───────────────┐   HTTPS   ┌─────────── RAILWAY ──────────┐
│  apps/mac_agent/voice_loop.py     │  bearer-  │  FastAPI orchestrator        │
│  mic capture / afplay playback    │  token    │  Postgres (pgvector)         │
└───────────────────────────────────┘──────────►│  Redis                       │
                                                └──┬──────────┬──────────┬─────┘
                                                   │          │          │
                                                ┌──▼──┐   ┌───▼───┐  ┌───▼────────┐
                                                │Groq │   │OpenAI │  │ElevenLabs  │
                                                │chat │   │embeds │  │TTS         │
                                                │+STT │   │       │  │            │
                                                └─────┘   └───────┘  └────────────┘
```

## Stack (per layer)

| Layer | Default | Local-dev alternative |
|---|---|---|
| LLM (chat) | Groq `gemma2-9b-it` via OpenAI-compatible API | Ollama (`LLM_PROVIDER=ollama`) |
| STT | Groq `whisper-large-v3-turbo` | `faster-whisper` (`STT_PROVIDER=local`) |
| Embeddings | Voyage AI `voyage-3-lite` (512-dim, free tier) | Ollama embeddings (`EMBED_PROVIDER=ollama`) |
| TTS | ElevenLabs streaming | — |
| Memory | Postgres + pgvector | same (local Docker container) |
| Auth | `Authorization: Bearer ${VAULT_API_KEY}` | empty key disables auth |

## Repo layout

```
vault-zeta/
├── apps/
│   ├── api/                 FastAPI app + middleware + routers
│   │   ├── main.py
│   │   ├── middleware/auth.py
│   │   ├── routers/{chat,voice,console}.py
│   │   └── static/          web console SPA (index.html, app.js, styles.css)
│   └── mac_agent/           push-to-talk Mac client (not shipped to Railway)
├── core/
│   ├── brain/llm.py         multi-provider LLM client
│   ├── memory/{db,embedder,episodic,semantic,migrate}.py
│   ├── voice/{stt_whisper,tts_elevenlabs}.py
│   ├── identity/self_model.py
│   ├── config.py
│   └── logging.py
├── infra/
│   ├── docker/docker-compose.yml   local Postgres + Redis
│   └── migrations/                 001 initial, 002 vector dim bump
├── Dockerfile               slim runtime for Railway
├── railway.toml
└── pyproject.toml
```

---

## Setup — local-dev path

Run everything against the cloud LLM / STT / embeddings, but with the API and Postgres still on your Mac. This is how you validate before paying for Railway.

1. **Get the keys**
   - Groq: https://console.groq.com — chat + STT, free tier covers personal use
   - Voyage AI: https://www.voyageai.com — embeddings, 200M tokens/mo free
   - ElevenLabs: already configured
2. **Fill `.env`** with `LLM_API_KEY`, `EMBED_API_KEY`, `STT_API_KEY` (Groq key works for both LLM_ and STT_), `ELEVENLABS_API_KEY`. Leave `VAULT_API_KEY` empty for now.
3. **Bring up local Postgres + Redis**:
   ```bash
   bash scripts/bootstrap.sh
   ```
4. **Install** (default install no longer pulls `faster-whisper`):
   ```bash
   source .venv/bin/activate
   pip install -e ".[mac]"        # mac extras only; add 'whisper_local' if you need offline STT
   ```
5. **Start the API**:
   ```bash
   uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000
   ```
6. **Probe**:
   ```bash
   curl -s http://127.0.0.1:8000/v1/health | jq .
   ```
7. **Voice loop on the Mac**:
   ```bash
   python -m apps.mac_agent.voice_loop
   ```
   `VAULT_API_BASE` defaults to localhost; the mac_agent auto-loads `.env`.

---

## Deploy to Railway

The `railway:use-railway` skill in this repo automates this. Steps it handles:

1. `railway init` — create project, link this directory
2. Provision plugins: **Postgres** + **Redis**
3. Set environment variables from `.env` (omit local-only `POSTGRES_*` — Railway injects `DATABASE_URL`)
4. Set `VAULT_API_KEY` to a freshly generated `openssl rand -hex 32`
5. `railway up` — deploys the Dockerfile

On first boot, the lifespan hook in `apps/api/main.py`:
- Connects to Railway Postgres via `DATABASE_URL`
- Runs `core/memory/migrate.py` → applies `001_initial.sql` then `002_embedding_dim_1536.sql`
- Mounts the bearer-auth middleware
- Probes Groq / OpenAI / ElevenLabs on first health call

After deploy:

```bash
# 1. Verify the deployment
export VAULT_URL="https://<project>.up.railway.app"
export VAULT_API_KEY="<the key you set on Railway>"
curl -s -H "Authorization: Bearer $VAULT_API_KEY" $VAULT_URL/v1/health | jq .

# 2. Point the Mac at Railway — edit .env on your Mac:
VAULT_API_BASE=https://<project>.up.railway.app
VAULT_API_KEY=<same>

# 3. Run the voice loop
python -m apps.mac_agent.voice_loop
```

Activity Monitor on the Mac should now stay quiet during replies. That's the win condition.

---

## Smoke tests

```bash
# Health (open endpoint)
curl -s $VAULT_URL/v1/health | jq .

# Chat — streaming SSE
curl -N -X POST $VAULT_URL/v1/chat \
  -H "Authorization: Bearer $VAULT_API_KEY" \
  -H 'content-type: application/json' \
  -d '{"message":"In one sentence, who are you?"}'

# TTS — text in, MP3 out
curl -X POST $VAULT_URL/v1/voice/speak \
  -H "Authorization: Bearer $VAULT_API_KEY" \
  -H 'content-type: application/json' \
  -d '{"text":"Scrappy online."}' \
  --output reply.mp3 && open reply.mp3

# STT — audio in, transcript out
curl -X POST $VAULT_URL/v1/voice/transcribe \
  -H "Authorization: Bearer $VAULT_API_KEY" \
  -F "audio=@some_clip.wav" | jq .
```

---

## Memory layer (unchanged)

Every turn:
1. Embed user message → store in `messages` (1536-dim now).
2. Pull recent window (last 12 turns) from this session.
3. Vector-search `memories` for top 8, ranked by `similarity * (0.5 + 0.5*importance)`.
4. Render live self-context (mission, days remaining, model, capabilities).
5. Compose: system persona → self-context → retrieved memories → recent → new turn.
6. Stream the reply, persist with embedding on completion.

The **Memory Keeper** expert now writes durable facts to `memories` via the
`memory.save` tool (and recalls them with `memory.search`), so the store fills as
you talk. Nightly auto-consolidation (critic/learner) is still future work; you
can also drop facts/reflections in directly to test retrieval.

---

## Web console

Browsing the deployment's root URL (`/`) serves a self-contained management
console — a vanilla SPA (no build step) served straight from FastAPI out of
`apps/api/static/`. It talks to the same API over the same origin, so there's
no CORS and no separate deploy. Four tabs:

- **Chat** — stream a conversation with Scrappy. Tool calls and expert
  delegations show up inline as chips so you can see what he did to answer.
  (Uses `fetch` + `ReadableStream` to read the SSE stream, since the browser's
  `EventSource` can't send the bearer header.)
- **Memory** — browse, semantically search, hand-add, and delete entries in the
  pgvector store (`/v1/console/memory`).
- **System** — which connectors are installed and which experts are live
  (with health), straight from `/v1/console/status`.
- **Guide** — a short "how to use Scrappy" primer.

Auth: the page shell (`/`, `/static/*`, `/status`) loads without a token so it
can prompt for the key; every `/v1` data call carries
`Authorization: Bearer <VAULT_API_KEY>`. The key is held in the browser's
localStorage (gear icon, top-right). In open mode (empty `VAULT_API_KEY`) no key
is needed.

## Agent architecture: orchestrator + experts

Scrappy is an *orchestrator*. Each turn runs a multi-turn tool loop
(`core/brain/agent_loop.py`): the model can call a tool, see the result, and keep
going until it answers without calling one. Capped by `TOOL_LOOP_MAX_ITERS`.

Two kinds of tools sit behind one merged toolbox (`core/brain/orchestrator.py`):

- **Connectors** (`core/connectors/<name>/connector.py`) — raw capabilities.
  Tools marked `executor="server"` run in-process (e.g. `memory.*`) and their
  results are fed back to the model; `client_mac` tools are forwarded to the Mac
  over SSE (fire-and-forget) and only offered on voice channels.
- **Experts** (`core/agents/experts.py`) — specialist sub-agents Scrappy
  delegates to via auto-generated `ask_<name>` tools. An expert has its own
  persona, its own slice of connectors, and its own internal tool loop, and
  hands back a synthesized answer. Current roster: **Memory Keeper** (owns
  long-term memory via the `memory` connector) and **Strategist** (pure
  reasoning, no tools).

An expert is only offered once every connector it depends on is installed, so
the roster self-assembles. Server tools and experts work on every channel; Mac
control is added only on voice.

### Add an expert
1. If it needs tools, add a connector under `core/connectors/<ns>/connector.py`.
2. Add a `SubAgentSpec` to `core/agents/experts.py` with its `tool_namespaces`.

That's it — `ask_<name>` appears in Scrappy's toolbox automatically.

## What's next (per the architecture)

1. ✓ Voice loop (Whisper + ElevenLabs) on the Mac agent
2. ✓ Cloud deployment (Railway + Groq + OpenAI)
3. ✓ Connector framework + Mac control connector
4. ✓ Multi-turn agentic tool loop + sub-agent (expert) framework
5. ✓ Long-term memory tools (Memory Keeper expert)
6. ✓ Gmail connector → Email expert
7. ✓ Web console (chat + memory browser + system status + guide)
8. Calendar connector → calendar expert
9. Goals/tasks + planner/executor expert
10. Critic + Learner + nightly memory consolidation

## Copyright & Credits

**Designed & Developed by Karnveer Singh** — [www.karnveer.com](https://www.karnveer.com)

© 2026 Karnveer Singh. Scrappy Singh and the Vault Zeta node are his work.
