# Continuity Backup Policy

The goal is to remove any single provider/account as the owner of Scrappy's continuity.

## 3-2-1 target

Maintain three independently recoverable copies across two storage classes, with one offline copy:

1. **Live state** — Vault Zeta database/runtime.
2. **Encrypted secondary** — operator-owned private object storage or private repository containing only the encrypted `.enc` artifact plus non-secret checksum metadata.
3. **Offline encrypted copy** — separate local/removable storage that is not continuously mounted to the live system.

## What may be stored beside the encrypted artifact

Safe non-secret metadata:

```text
filename
created_at
capsule_schema_version
cipher/KDF identifier
byte size
SHA-256 of encrypted bytes
restore-drill date/result
```

Do not store the passphrase, API credentials, plaintext export, plaintext capsule, decrypted Vault bundle, or private continuity spec beside the backup.

## Rotation

- Keep several dated encrypted artifacts rather than silently overwriting the only good copy.
- Verify SHA-256 after copying.
- Periodically perform a clean-environment dry-run restore.
- Before deleting an older generation, confirm at least two other verified encrypted generations remain.

## Account-loss procedure

If a model-provider account becomes unavailable:

1. Do not treat provider loss as continuity loss until the independent backup is checked.
2. Verify the encrypted capsule locally.
3. Bring up a compatible fresh Vault/runtime under a provider or local model that the operator controls.
4. Run dry-run restore, review, then explicitly apply.
5. Bootstrap the fresh model from the verified provider-neutral context.
6. Run continuity evals and mark unsupported history `UNKNOWN`.

This procedure restores explicit working continuity; it does not claim a literal conscious process was transferred.
