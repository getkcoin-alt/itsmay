# It's May — Golden Path & Regression Checklist

The **one workflow** It's May must do flawlessly (the wedge), plus a manual
checklist to run on a real Mac before each release. If any box fails, the core
loop isn't trustworthy yet — fix it before widening.

> Backs `docs/strategy.md` (the wedge) and Epic 1 of `docs/backlog.md`.

## Setup (once)

```bash
cd ~/itsmay
git checkout main && git pull
source .venv/bin/activate
pip install -e ".[mac]"
export VAULT_API_BASE=https://<your-app>.up.railway.app
export VAULT_API_KEY=<key>
# optional: let Claude Code edit without prompts, and stack Groq keys
export SCRAPPY_CLAUDE_FLAGS="--permission-mode acceptEdits"
# (set LLM_API_KEY="k1,k2,k3" in Railway for headroom)
scrappy seed        # one-time: load long-term memory
```

## The golden path (the demo)

Two terminals, venv active in both.

```bash
# Terminal 1 — the executor on your Mac (leave running):
scrappy worker            # → ✓ connected — waiting for tasks

# Terminal 2 — confirm everything's green, then talk:
scrappy status            # ✓ api/llm/embedder · ✓ mac worker · ✓ memory · keys N/N active
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

Onboarding
- [ ] `pip install -e ".[mac]"` succeeds; `scrappy voice` starts hands-free (no ENTER per turn).
- [ ] `scrappy status` shows ✓ api/llm/embedder, worker state, memory count, and per-key health.

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
