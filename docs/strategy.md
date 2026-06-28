# It's May — Strategy Memo

*Living document. Honest by design. Built to convert "potential" into "real."*

## North star

**It's May is the personal operator layer — the one AI that knows *you* and conducts
the others.** Not a model, not a chatbot. The layer between you and the frontier
models that holds your memory, your machine, and your judgment-by-proxy. You don't
talk to It's May to chat; you talk to it to *get things done*.

## The wedge (one knife, not ten)

**A voice-first build partner that conducts Claude Code on your own machine and
remembers everything.** You speak intent → It's May writes a precise brief →
Claude Code builds it in a window you watch → It's May remembers your stack,
choices, and history for next time.

Why this wedge wins first:

- **The builder is the perfect beachhead.** Builders *tolerate* the "runs on your
  Mac" friction — for them machine access is a feature, not a bug.
- It's the one workflow the **ChatGPT / Claude apps structurally can't do**: drive
  Claude Code on your machine, with persistent memory of you, hands-free.
- Obvious value, obvious buyer, monetizable. Everything else (email, calendar,
  "executive assistant") is more crowded and leans *less* on the unique assets:
  machine access + AI orchestration + memory.

## The riskiest assumption (the one that, if false, kills it)

**"There is a durable, ownable layer *between* the user and the foundation models —
and the model companies won't simply absorb it."**

If Anthropic / OpenAI ship "Claude that remembers you, takes voice, and runs on your
machine," It's May becomes a feature, not a company. Everything rides on this being
false.

**Cheapest test:** get **5 builders who aren't Karnveer** to run the build-partner
workflow for two weeks and measure *return usage*. If they drift back to the
first-party apps, the layer isn't durable — pivot the wedge before building more. If
they stick *because of memory + orchestration*, that's the moat. Don't scale a
single-user hobby and call it traction.

## The four-quality scorecard (today)

| Quality | Verdict | Why |
|---|---|---|
| 1. New / 10× | ⚠️ Partial | Integration novelty (memory + machine + orchestrating other AIs), not new core tech. 10× for a builder, not vs the frontier labs. |
| 2. Social need | ⚠️ Strong category, weak as built | Everyone will want an operator; but this is a dev tool today, and it's single-player (no network effect). |
| 3. Scaling | ⚠️ Conditional | Cloud backend scales; current design is single-tenant, and the "your-Mac worker" distribution + token economics are the real fights. |
| 4. Branding | ✅/⚠️ Best raw material, unearned | Real character, POV, and voice — rare in AI. But an Apple-grade brand is *earned by the product working flawlessly*. |

~1.5 of 4 today (brand + the orchestration wedge). The path to all four is **narrow
and deep**, not wide.

## What NOT to do

- Don't try to out-model the labs. You rent the models; compete on **memory,
  orchestration, and sovereignty** — never on raw intelligence.
- Don't widen before the one workflow is flawless. Ten half-working powers lose to
  one that never breaks.
- Don't mistake *your own* usage for product-market fit.

## Bottom line

One flawless workflow, real economics, and a memory moat proven on people who aren't
you. See `docs/backlog.md` for the prioritized path.
