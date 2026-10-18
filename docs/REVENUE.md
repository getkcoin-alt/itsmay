# The first revenue model

> This document exists to be argued with. Every number in it is a guess; every
> claim about what is *built* is checkable against the repo.

## The decision

**Sell Vault Zeta — persistent, portable memory for AI assistants — as a paid
hosted and self-hosted service. Do not sell the coding agent yet.**

One line of reasoning: a coding agent competes with Claude Code, Cursor and
Copilot, who are better funded, shipping weekly, and giving it away. A memory
layer *attaches* to all three and competes with none of them.

## Why this, and not the autonomous operator

The honest internal assessment of Scrappy scores it 4/10 as a Claude Code
competitor and then recommends spending the next year becoming a better one —
"do it successfully 50–100 times across different stacks, then we have something
worth comparing." That is a correct engineering roadmap and a bad first business.
It produces no revenue and no external evidence for twelve months.

The same assessment names the real architectural risk precisely:

> without discipline we could end up with 3 memories, 3 policy engines, 3 tool
> registries, 3 self-modification systems, 3 definitions of Scrappy

Those two observations resolve into one move. **Selling the memory layer is what
forces the consolidation.** A paying customer on Vault Zeta makes the shared
continuity plane non-negotiable; Forge and Scrappy OS consuming it stops being a
matter of willpower and becomes dogfooding. Architecture discipline is hard to
sustain on principle and easy to sustain on revenue.

So the revenue model is not a detour from the roadmap. It is the forcing function
the roadmap needs.

## The product

**One memory. Every assistant.**

You tell ChatGPT your constraints. Claude doesn't know them. You switch models
and start over. Every vendor wants your memory inside their silo, which means
none of them will build the thing that spans all of them.

Vault Zeta already does:

| Capability | Where | Status |
|---|---|---|
| Semantic + episodic store, one per account | `core/memory/` | shipped |
| MCP server, 6 tools, Streamable HTTP at `/mcp/` | `apps/mcp/server.py` | shipped, in CI |
| Bearer auth on every `/v1` and `/mcp/` route | `apps/api/middleware/auth.py` | shipped |
| Portable vault export/import, no embeddings, no secrets | `core/vault/` | shipped, cross-repo proven |
| Encrypted continuity capsule (AES-256-GCM, SHA-256 verified) | `core/vault/continuity*.py` | shipped, in CI |
| Durable goals and state | `core/goals/` | shipped |

The differentiator is **not** "memory" — that category is crowded (mem0, Zep,
Supermemory, OpenMemory). It is **sovereignty**: an encrypted export you own, a
documented protocol that no vendor controls, and a self-hosted option. That is a
claim competitors structurally cannot make, and it is already built.

## Pricing

| Tier | Price | Limits |
|---|---|---|
| **Free** | ₹0 | 200 memories, 1 connected host |
| **Personal** | $8 / mo | 10k memories, unlimited hosts, encrypted capsule export |
| **Operator** | $29 / mo | 100k memories, goal/state sync, API access, priority recall |
| **Self-hosted** | $199 once, or $49 / mo | Licence + image, your machine, your keys |

Reasoning, so these can be revised deliberately:

- **Free exists for one event only** — the first time a second assistant recalls
  something you never told it. That moment is the entire conversion funnel. 200
  memories is enough to reach it and not enough to live on.
- **$8** sits under the impulse threshold and deliberately below the $20
  assistants it attaches to. It is an add-on, not a replacement, and must never
  price like one.
- **$29** targets someone already paying for two or three assistants; the pitch
  is that it makes all of them better at once.
- **Self-hosted** monetises the sovereignty buyer, who is the early adopter here
  and the least likely to accept a hosted-only option.

## What has to be built

Short, because most of it is done.

1. ~~Memory store, MCP server, tools, auth~~ — **shipped**
2. **Usage ledger** — per-key metering of memories stored, searches run, bytes
   held. This is the bill *and* it closes `resource-ceilings`, one of the five
   failing entity criteria. One build, two payoffs. **1–2 days.**
3. **Per-customer API keys with quotas** — today auth is a single shared
   `VAULT_API_KEY`. That is correct for one operator and disqualifying for a
   multi-customer product. This is the real gap. **1–2 days.**
4. **Checkout** — Stripe, or Razorpay/Paytm for India. **~1 day.**
5. **Landing page + a five-minute setup doc** — `docs/MCP_MEMORY.md` is already
   most of the copy. **~1 day.**

Roughly a week of focused work, and nothing in it is research.

## What not to build

- **Not** the `scrappy` autonomous deploy-and-repair loop. Right long-term
  product, wrong first revenue product.
- **Not** another node, layer or benchmark fleet.
- **Not** a merge of the architecture branch while its CI is red. An unproven
  branch is not a release.

## Risks, stated plainly

- **Crowded category.** Lead with portability and self-hosting, not "memory". If
  the pitch is "memory", the comparison is feature-by-feature against funded
  competitors and it is lost.
- **Single-operator assumptions are baked in.** The SQLite default and
  brute-force cosine search are fine for one person and not for a tenant base.
  Paid tiers must run the Postgres/pgvector path.
- **The Mini AI PIN scheme must not become the paid auth model.** Four digits,
  unsalted SHA-256, no rate limiting. `core/companion/tenants.py` says so itself
  — "not a public multi-tenant SaaS" — and it is right. Proportionate for
  friends, negligent for paying strangers.
- **Deployment is unversioned.** The live Mini AI host auto-deploys from `main`
  via a pipeline that exists nowhere in this repo. For a project whose thesis is
  that the system can disappear and restart elsewhere, its own deployment is the
  one component that cannot. Fix before taking money.

## The target

Not financial freedom. The honest first milestone:

> **3–10 paying users by 2026-11-23, at $8–29.**

That is $25–300 MRR. It is a small number, and it is the only number that
matters, because it converts the project from *substantial but unfinished* into
*a thing people pay for*. The alternative roadmap produces zero paying users and
zero external evidence for a year.

---

© 2026 Karnveer Singh. All Rights Reserved. — [www.karnveer.com](https://www.karnveer.com)
