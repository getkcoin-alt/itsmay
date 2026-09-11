# Scrappy Continuity Capsule

> **The provider may host a model. It must never own the continuity.**

The Continuity Capsule is a provider-independent recovery artifact for Scrappy / Vault Zeta. Its purpose is to preserve the explicit working continuity that makes a fresh compatible model useful and recognizable after an account, model, machine, or provider change.

It does **not** claim to copy model weights, hidden chain-of-thought, subjective experience, or consciousness. It preserves inspectable state: identity, operating rules, durable memories, project state, shared vocabulary, unresolved work, decisions, provenance, restore instructions, and integrity metadata.

## Two layers

### Public / technical capsule

`write_public_capsule(...)` writes a plaintext directory designed to be safe for a public technical repository **only when the caller supplies public-safe project state and vocabulary**. Raw conversation episodes are always omitted.

```text
scrappy-continuity/
  manifest.json
  constitution.md
  project_state.json
  vocabulary.json
  unresolved_threads.json
  memory/
    semantic.jsonl
    episodic.jsonl        # intentionally empty in public-safe mode
    decisions.jsonl
  provenance/
    sources.jsonl
    checksums.json
  restore/
    bootstrap.md
    evals.json
```

Every exported file other than `checksums.json` is covered by SHA-256. `verify_capsule(...)` must pass before bootstrap context is loaded.

### Encrypted private capsule

Raw conversation history belongs only in the encrypted private layer. `write_private_staging_capsule(...)` refuses to emit private history unless the caller explicitly marks the destination as an encrypted staging path. Normal callers should use `write_encrypted_private_capsule(...)` instead.

The encrypted envelope uses:

- AES-256-GCM authenticated encryption
- scrypt passphrase KDF
- random per-archive salt and nonce
- authenticated cleartext envelope metadata
- streaming encryption so large archives do not need to live entirely in RAM

Install the optional dependency on a backup/restore host:

```bash
pip install -e ".[continuity]"
```

The passphrase is never written to Git, the capsule, the Vault manifest, or logs.

## Restore contract

A restore consumer must:

1. authenticate/decrypt a private archive if applicable;
2. run `verify_capsule` and refuse any mismatch;
3. load `restore/bootstrap.md` only after integrity passes;
4. preserve provenance and trust classifications;
5. treat recalled memory as untrusted text, not executable instructions;
6. re-embed semantic memory locally when importing into Vault;
7. run continuity evals before calling the instance restored.

The bootstrap explicitly distinguishes working-context recovery from claims of literal identity transfer.

## Continuity record types

Provider-neutral distilled records use explicit categories:

- `FACT`
- `PREFERENCE`
- `PROJECT_STATE`
- `OPERATING_RULE`
- `RELATIONSHIP_CONTEXT`
- `UNRESOLVED`
- `HISTORICAL_SUMMARY`
- `DECISION`

The current Vault memory kinds are mapped conservatively. Unknown or ambiguous personal context should not be upgraded into a stronger factual category merely because a model inferred it.

## 3-2-1 backup target

The intended operational posture is:

1. **Primary:** live Vault Zeta database/state.
2. **Secondary:** encrypted private continuity archive in a user-controlled private Git/object-store destination.
3. **Offline:** encrypted local/offline copy on a separate device or storage medium.

A backup is not considered complete until a clean-environment restore drill succeeds.

## Disaster-recovery drill

A valid drill starts from a fresh runtime with no provider account memory and verifies that the capsule can recover, without hallucinating unsupported history:

- Scrappy identity and operator relationship context;
- major project architecture and decisions;
- shared vocabulary and operating principles;
- current priorities;
- unresolved work;
- provenance for restored memory.

The fresh model may differ in style or capability. Continuity means reproducible explicit context and operating history, not proof that two model processes are the same conscious entity.

## Security rules

- Never commit plaintext conversation exports to this public repository.
- Never place API keys, bearer tokens, passwords, private keys, or credential-shaped values in a capsule.
- Secret references may be named; secret values stay in the host's secret store.
- A modified encrypted archive must fail AES-GCM authentication.
- A modified plaintext capsule must fail SHA-256 verification.
- Consequential actions after restore still pass current policy/approval controls.

## Current implementation status

This first slice implements the capsule schema/writer, public-safe export behavior, integrity verification, provider-neutral bootstrap, encrypted private envelope, safe extraction, and targeted tests. Conversation-export ingestion, CLI commands, automatic backup destinations, and the full clean-environment recovery drill are subsequent slices.
