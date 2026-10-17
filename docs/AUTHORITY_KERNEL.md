# Vault Zeta Authority Kernel V0

This slice turns the architecture discussion into one falsifiable rule:

> No action executes unless a currently valid capability and a currently valid
> goal lease authorize it.

## Frozen V0 invariants

1. **DEFAULT DENY** — unknown or unauthorised actions fail closed.
2. **MEMORY IS NEVER AUTHORITY** — remembered approval is historical evidence,
   never an active permission.
3. **COMPETENCE IS NEVER AUTHORITY** — a skill or model being capable of an
   operation does not authorize it.
4. **AUTHORITY CANNOT SELF-EXPAND** — the executing model does not possess the
   signing key used to issue capabilities or leases.
5. **GOALS CANNOT OUTLIVE THEIR LEASE** — goals are prescriptive execution
   leases with explicit time/action budgets, not permanent memories.

## Deliberately excluded from V0

This branch does **not** wire the kernel into production execution yet. It also
does not implement delegation chains, taint tracking, differential privacy,
skill certification, semantic goal comparison, or MCP authorization.

Those features are downstream of the default-deny boundary and should not be
allowed to obscure whether the boundary itself works.

## Trust boundary

The kernel accepts externally issued Ed25519-signed artifacts and verifies them
against configured public keys. Private signing keys are intentionally outside
the kernel and outside model-accessible state.

The first tests prove:

- no capability -> deny;
- bad signature -> deny;
- wrong principal -> deny;
- expired goal -> deny;
- exhausted goal -> deny;
- parameter overflow -> deny;
- valid capability + valid goal -> allow; and
- historical permission in memory cannot authorize execution.

The next milestone, only after these tests are green, is capability delegation
with the invariant:

`child_authority ⊆ parent_authority`.
