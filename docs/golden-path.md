# It's May — Golden Path & Regression Checklist

The **one workflow** It's May must do flawlessly (the wedge), plus a manual
checklist to run on a real Mac before each release. If any box fails, the core
loop isn't trustworthy yet — fix it before widening.

> Backs `docs/strategy.md` (the wedge) and Epic 1 of `docs/backlog.md`.

## Setup (once)

One command — installs everything, asks for your Groq key, writes
`~/.itsmay/config.env`. Fully local: no Railway, no Postgres, no Docker.

```bash
curl -fsSL https://raw.githubusercontent.com/getkcoin-alt/itsmay/main/scripts/install.sh | bash
```

(Stack multiple Groq keys for headroom: `LLM_API_KEY=k1,k2,k3` in
`~/.itsmay/config.env`.)

## The golden path (the demo)

Three terminals, venv active in each (`cd ~/itsmay && source .venv/bin/activate`).

```bash
# Terminal 1 — the brain, on your Mac (sovereign; no cloud):
scrappy serve             # memory: sqlite (local)

# Terminal 2 — the executor on your Mac (leave running):
scrappy worker            # → ✓ connected — waiting for tasks

# Terminal 3 — confirm everything's green, seed once, then talk:
scrappy status            # ✓ api/llm/embedder · ✓ mac worker · ✓ memory · sqlite (local) · keys
scrappy seed              # one-time: load long-term memory
scrappy voice             # hands-free; just talk
```

Then, by voice:
1. "Build me a CLI that lists my ten biggest files in Downloads."
   → Scrappy writes a refined prompt and **opens one Claude Code window** building it.
2. "Now add a `--json` flag."
   → goes to the **same** Claude Code session (no second window).
3. "What did we just build?"
   → answers from context, **no needless expert/agent call**.
4. "Remember I prefer Typer for CLIs."
   → saves to memory; recalls it next session.

## Regression checklist (run on a clean Mac)

Onboarding (IM-2.1 / #7)
- [ ] `curl … install.sh | bash` on a clean Mac: zero → `scrappy status` green in < 5 min, no manual venv/keys/Railway.
- [ ] `scrappy serve` starts the local backend; `scrappy status` shows memory · **sqlite (local)** (no Postgres/Docker).
- [ ] `scrappy voice` starts hands-free (no ENTER per turn); per-key token budget + ⚠️ low show in `scrappy status`.

Honest mode (IM-1.1 / #4)
- [ ] "Set up Shopify for me" → Scrappy says it needs a human signup and offers to open the page / build the automatable parts. **No** theatre-acting agent, **no** fake "done."
- [ ] An agent that does nothing reports honestly (never "(completed)" with no result).

Worker preflight (IM-1.2 / #5)
- [ ] Worker **off** + a Mac task → Scrappy says the worker isn't connected / to start it; doesn't silently run in the cloud.
- [ ] Worker **on** + spawn → runs on the Mac; you can watch it in the auto-opened watch window.

Graceful rate-limit (IM-1.3 / #6)
- [ ] On a 429, Scrappy **speaks** a calm message ("I'm rate-limited…") — never dumps a raw `[chat error] … 429` blob.

Claude Code session (persistent)
- [ ] "Code X" opens **one** Claude Code window; follow-ups go to the **same** window; you can type into it yourself.
- [ ] If you close the window, the next coding request opens a fresh one.

Restraint (IM-1.4)
- [ ] Chit-chat / opinions / quick questions are answered directly — Scrappy does **not** spin up an expert or agent for them.

Memory / hands-free
- [ ] Mid-call ENTER mutes/unmutes; Ctrl+C quits cleanly.
- [ ] Something said in one session is recalled in a later one.

## Known edges (not blockers, tracked)
- A follow-up sent after Claude Code has **exited** (window still open) lands on the shell, not Claude — harden later.
- Energy VAD calibrates ~0.4s at the start of each listen; keep quiet for that beat in a noisy room (`VAD_SILENCE_MS` to tune).
