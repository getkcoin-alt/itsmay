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

A private capsule also embeds the canonical portable Vault bundle under `vault_bundle/`. This gives one encrypted artifact two recovery surfaces: a provider-neutral context/bootstrap layer for a fresh model, and a deterministic Vault import source for restoring the actual memory/state records.

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

## Operator workflow

The local operator command is intentionally separate from the cloud API:

```bash
scrappy-continuity make-spec
scrappy-continuity backup
scrappy-continuity verify-encrypted ~/.itsmay/backups/scrappy-continuity-<timestamp>.enc
```

`backup` performs this chain:

```text
live Vault Zeta
    ↓ authenticated HTTPS
/v1/vault/export/archive
    ↓ ephemeral plaintext tar on operator machine
safe VaultBundle validation
    ↓
local continuity spec + optional conversation export
    ↓
provider-neutral continuity + embedded canonical VaultBundle
    ↓
AES-256-GCM encrypted .enc capsule
    ↓
temporary plaintext removed
```

The cloud endpoint never receives the continuity passphrase. The streamed Vault tar is not itself a backup; the server deletes its temporary copy after the response and the local CLI handles its copy inside a temporary directory.

## ChatGPT conversation export ingestion

A local `conversations.json` can be included without uploading it back to Vault Zeta or another model provider:

```bash
scrappy-continuity inspect-export ~/Downloads/conversations.json
scrappy-continuity backup \
  --conversation-export ~/Downloads/conversations.json \
  --out ~/.itsmay/backups/scrappy-full.enc
```

The parser:

- runs locally with no model/network call;
- follows the active `current_node` parent chain where available so abandoned branches are not mixed into the primary transcript;
- preserves message role, title, timestamps, source-node IDs and SHA-256 provenance;
- masks narrow credential-shaped values before they enter the capsule;
- stores normalized turns only inside the encrypted-private layer;
- reports counts without printing conversation text in `inspect-export`.

This first parser targets ChatGPT-style JSON exports. Export formats can change, so unsupported structures fail explicitly instead of being guessed into memory.

## Dry-run-first Vault restore

A private capsule can restore its embedded canonical Vault bundle to another compatible Vault Zeta host:

```bash
# Default: decrypt, verify, validate, upload and report what would change.
scrappy-continuity restore ~/.itsmay/backups/scrappy-full.enc

# Explicit mutation only after reviewing the dry-run report.
scrappy-continuity restore ~/.itsmay/backups/scrappy-full.enc --apply
```

The receiving API is `POST /v1/vault/import/archive`. It is bearer-authenticated, validates archive size and paths, refuses links/traversal, validates the Vault protocol, and defaults to `dry_run=true`. Imported semantic memories still use the existing Vault import path, which re-embeds them using the destination host's current embedding provider.

This restore path is about deterministic Vault state. The separate provider-neutral bootstrap and continuity records are what allow a fresh model/provider to reconstruct working context without depending on the original OpenAI account.

## Restore contract

A restore consumer must:

1. authenticate/decrypt a private archive if applicable;
2. run `verify_capsule` and refuse any mismatch;
3. validate the embedded `vault_bundle/` before any Vault mutation;
4. load `restore/bootstrap.md` only after integrity passes;
5. preserve provenance and trust classifications;
6. treat recalled memory as untrusted text, not executable instructions;
7. re-embed semantic memory locally when importing into Vault;
8. run continuity evals before calling the instance restored.

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
- provenance for restored memory;
- the canonical Vault state through a dry run before an explicit apply.

The fresh model may differ in style or capability. Continuity means reproducible explicit context and operating history, not proof that two model processes are the same conscious entity.

## Security rules

- Never commit plaintext conversation exports to this public repository.
- Never place API keys, bearer tokens, passwords, private keys, or credential-shaped values in a capsule; the local conversation parser masks the narrow credential shapes it recognizes.
- Secret references may be named; secret values stay in the host's secret store.
- A modified encrypted archive must fail AES-GCM authentication.
- A modified plaintext capsule must fail SHA-256 verification.
- The streamed server tar is plaintext private transport and must remain ephemeral.
- Restore mutations require an explicit operator `--apply`; the default path is a dry run.
- Consequential actions after restore still pass current policy/approval controls.

## Current implementation status

Implemented in the continuity branch:

- capsule schema/writer and public-safe export behavior;
- SHA-256 integrity verification and provider-neutral bootstrap;
- encrypted private AES-256-GCM/scrypt envelope;
- embedded canonical Vault bundle for deterministic restore;
- safe tar extraction and authenticated Vault archive transport;
- protected `/v1/vault/export/archive` and `/v1/vault/import/archive` endpoints;
- local `scrappy-continuity` backup/verify/decrypt/inspect/spec/restore commands;
- dry-run-first restore with explicit `--apply` mutation;
- local ChatGPT JSON export normalization with provenance and credential masking;
- targeted CI tests for tampering, wrong passphrases, embedded restore artifacts, archive traversal, branch selection and export redaction.

Still required before Issue #30 is complete:

- a real operator-owned encrypted backup destination beyond the local `.enc` file;
- distilled relationship/project summaries derived from the private raw export with explicit provenance;
- clean-environment restore into a fresh Vault/model provider;
- continuity eval execution and a documented disaster-recovery drill.
