# It's May — Engineering Backlog

Derived from the "3 things to build next" in `docs/strategy.md`. Three epics, each
mapped to a strategy goal. Priorities: **P0** = blocks the wedge, do now · **P1** =
next · **P2** = later. Sizes: **S** ≤ a day · **M** a few days · **L** a week+.

Sequencing rule: **finish Epic 1 to "never breaks" before widening.** Epics 2 and 3
run in parallel once Epic 1's P0s are green.

---

## Epic 1 — One workflow, flawless + honest ✅ COMPLETE
*Goal: earn the brand (#4), unlock the need (#2). The voice → Claude Code → memory
loop must never break or lie.*

All shipped: honest mode (#4), worker preflight (#5), graceful rate-limit voice
(#6), curb-delegation (IM-1.4), golden-path checklist (`docs/golden-path.md`), plus
one-persistent-Claude-Code-session. The core loop no longer lies, runs Mac work
in the cloud silently, or dumps raw rate-limit errors.

### IM-1.1 · Honest mode: stop faking non-doable tasks · **P0 · M**
Scrappy currently spawns shell agents for things that aren't shell-doable (e.g.
"set up Shopify"), which "complete" having done nothing.
- Detect signup-based / browser-only / account-bound tasks and **say so**, then offer
  the real path (open the browser, guide step-by-step) instead of spawning an agent.
- Add a guard + system-prompt rule: never claim a task is done without verifiable
  output.
- **Done when:** asking Scrappy to "set up Shopify" yields an honest answer + a real
  next step, not a theatre-acting agent.

### IM-1.2 · Worker preflight for machine tasks · **P0 · S**
Before spawning an agent / `mac.claude_code`, check the worker is connected.
- If `scrappy status` worker is offline, Scrappy says "start `scrappy worker` first"
  rather than running invisibly on Railway.
- **Done when:** machine tasks never silently execute in the cloud; the user is told.

### IM-1.3 · Graceful rate-limit UX in voice · **P0 · S**
A raw `[chat error] ... 429` mid-conversation is brand-killing.
- When all keys are exhausted, Scrappy *speaks* it ("I'm rate-limited right now,
  give me a bit") instead of dumping a JSON error.
- Surface key health by voice on request ("how are my keys?").
- **Done when:** hitting the cap produces a calm spoken message, never a stack-tracey error.

### IM-1.4 · Curb reflexive delegation · **P1 · S**
Scrappy delegates to experts / spawns agents for casual turns, burning tokens and latency.
- Prompt-tune: answer directly; only delegate or spawn when the task genuinely needs it.
- **Done when:** a chit-chat turn makes 1 LLM call, not 3–5.

### IM-1.5 · Golden-path demo + manual test checklist · **P1 · S**
- A scripted end-to-end run of the wedge workflow + a checklist to verify nothing
  regressed before each release.
- **Done when:** the checklist exists and passes on a clean Mac.

---

## Epic 2 — Economics + distribution (the scaling spine)
*Goal: unlock scale (#3). Sane unit economics and zero-friction onboarding.*

### IM-2.1 · Kill install friction (one-command or hosted) · **P0 · L**
Today onboarding needs a venv, API keys, and Railway knowledge — a developer's setup.
- Decide: hosted backend (multi-tenant) vs a one-command installer (`curl | bash` /
  packaged app). Spike both, pick one.
- **Done when:** a non-developer can go from zero to talking to Scrappy in < 5 minutes.

### IM-2.2 · Usage + cost visibility · **P0 · M** ✅ DONE (#8)
- `scrappy status` now shows, per key, `remaining / limit tokens left (X%)` read from
  Groq's own rate-limit headers, plus a ⚠️ low flag and a headline warning under 15%
  of the daily token budget — accurate and restart-proof (no flaky in-process meter).
- **Done when:** the user can see "X% of today's budget left" before they hit the wall. ✓
- *Note:* the meaningful budget on Groq's free tier is the tokens-per-day cap (shown),
  not a dollar figure ($0 on free tier); a real cost estimate lands with paid tiers.

### IM-2.3 · Route experts to the cheap model too · **P1 · S** ✅ DONE
Finished the fan-out cost cut started by routing terminal agents to 8b.
- `experts_use_agent_model` (default on) auto-routes tool-using experts
  (memory/email/researcher) to `llm_agent_model`; the Strategist is marked
  `heavy=True` and stays on 70b for reasoning quality. An explicit `spec.model`
  always wins. Cheap clients are cached per model (no httpx leak per delegation).
- **Done when:** an expert-heavy turn no longer spends 70b tokens for routine
  sub-tasks. ✓ — only pure-reasoning delegation now touches the big model.

### IM-2.4 · Multi-tenancy foundation · **P1 · L**
Current design is single-tenant: hardcoded `user_handle`, in-memory agent store and
worker bridge tied to one process.
- Spike per-user scoping + auth; replace in-memory singletons with per-user state.
- **Done when:** two users can run isolated sessions on one backend.

### IM-2.5 · Trim the ~2.5k-token tool payload · **P2 · M**
21 tool schemas ride on every LLM call.
- Expose tools contextually (only what the turn plausibly needs) instead of all at once.
- **Done when:** average prompt tool-overhead drops materially with no capability loss.

---

## Epic 3 — Memory moat + outside validation
*Goal: earn the 10× (#1) and test the riskiest assumption. Memory is the one edge the
stateless lab apps don't have.*

### IM-3.1 · 5 outside users on one workflow · **P0 · M**
- Recruit 5 builders who aren't Karnveer; define the single workflow they run for two weeks.
- **Done when:** 5 users are live and instrumented.

### IM-3.2 · Retention instrumentation · **P0 · M**
- Track return usage, session count, and a "week-4 vs day-1" value signal.
- **Done when:** there's a dashboard answering "do they come back, and does it get
  better the longer it knows them?"

### IM-3.3 · Memory quality loop · **P1 · M**
- Make nightly consolidation actually run on schedule; add dedup + importance decay;
  measure recall hit-rate.
- **Done when:** memory stays sharp over weeks instead of bloating, with a measured
  recall metric.

### IM-3.4 · "What It's May knows about you" surface · **P1 · M**
- Let users view and edit their stored memory (trust + the moat made visible).
- **Done when:** a user can open their memory, correct it, and delete entries.

### IM-3.5 · Memory export / portability · **P2 · S**
- One-click export of a user's memory. (Switching cost is the moat, but trust requires
  the door be unlocked.)
- **Done when:** a user can export everything It's May knows about them.

---

## Right-now shortlist (the next 5 things, in order)
1. ~~**IM-1.1** Honest mode~~ ✅
2. ~~**IM-1.3** Graceful rate-limit voice UX~~ ✅
3. ~~**IM-1.2** Worker preflight~~ ✅
4. ~~**IM-2.2** Usage + cost visibility~~ ✅ (#8)
5. **IM-2.1** Kill install friction (#7) — *the long pole; spike hosted vs one-command next*
