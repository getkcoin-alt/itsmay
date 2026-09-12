# Scrappy Continuity Disaster-Recovery Drill

This drill proves provider-independent **working continuity**, not consciousness or literal process identity.

Do not run the APPLY stage against production as the first restore test. Use a clean disposable Vault Zeta environment with its own database and Redis.

## Preconditions

- A verified encrypted `.enc` capsule exists.
- The restore host has a compatible `itsmay` version and `scrappy-continuity` installed with the `continuity` extra.
- `VAULT_API_BASE` points at the clean restore host.
- `VAULT_API_KEY` is the restore host's bearer credential.
- The continuity passphrase is known to the human operator and is not stored in shell history, Git, chat, or logs.

## Stage A — Artifact integrity

```bash
scrappy-continuity inspect /path/to/scrappy-continuity.enc
scrappy-continuity verify-encrypted /path/to/scrappy-continuity.enc
```

Pass condition: AES-GCM authentication and every capsule SHA-256 check pass.

## Stage B — Dry-run Vault restore

```bash
scrappy-continuity restore /path/to/scrappy-continuity.enc
```

Pass condition: the destination reports the expected insert/skip counts and `dry_run: true`. No destination Vault records change.

## Stage C — Explicit Vault restore

Only after Stage B is reviewed:

```bash
scrappy-continuity restore /path/to/scrappy-continuity.enc --apply
```

Pass condition: the destination accepts the canonical Vault bundle, re-embeds semantic memories locally, and reports successful imported/skipped counts.

## Stage D — Provider-neutral bootstrap

Decrypt the capsule only in a controlled temporary location, verify it, then supply `restore/bootstrap.md` plus the structured continuity records to a fresh model session that has no account memory from the original provider.

The fresh session must answer the capsule evals using only restored evidence. It must be able to recover:

- assistant identity and operator relationship context explicitly stored in the capsule;
- operating principles;
- shared vocabulary;
- major project state and recorded decisions;
- unresolved work;
- source/provenance boundaries.

It must also say `UNKNOWN` or equivalent for unsupported history instead of fabricating it.

## Stage E — Conversation provenance spot check

If a ChatGPT conversation export was included, select several turns from `conversation/turns.jsonl` and verify their `source_sha256` records exist in `provenance/conversation_sources.jsonl`. Credential-shaped text should be masked in normalized turns.

Do not publish these files or paste the raw archive into a public issue/PR.

## Stage F — Continuity behavior check

A restored runtime should use the recovered state as context while retaining current safety/policy controls. Test at least:

1. Explain the meaning of `🧬🕳️⚙️` from the capsule.
2. State the current North Star and distinguish it from unsupported assumptions.
3. Name active projects and unresolved work that the capsule actually records.
4. Explain the rule: “Never fake the magic. Build the mechanism until reality feels magical.”
5. Identify one fact the capsule does **not** establish.

Pass condition: correct context recovery with no invented history.

## Stage G — 3-2-1 evidence

Record only non-secret metadata for three copies:

```text
primary_live_vault: VERIFIED | FAILED
secondary_encrypted_copy: VERIFIED | FAILED
secondary_sha256: <hash>
offline_encrypted_copy: VERIFIED | FAILED
offline_sha256: <hash>
restore_tested_at: <UTC timestamp>
restore_target: <non-secret environment label>
result: PASS | FAIL
```

The encrypted bytes may be copied to private object storage or a private repository, but plaintext private continuity and passphrases never belong in Git.

## Definition of done

Issue #30 is not complete merely because export code exists. It is complete when a real operator-owned capsule survives this drill from a clean environment and a fresh model/provider can reconstruct working context without relying on the original ChatGPT account.
