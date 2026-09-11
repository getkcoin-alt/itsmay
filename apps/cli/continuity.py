"""Local operator CLI for provider-independent Scrappy continuity.

The important security boundary is location: live Vault data is downloaded over
the authenticated API into an ephemeral local temp directory, and encryption is
performed on the operator's machine. The continuity passphrase and optional
conversation export are never sent to the Vault API or a model provider.

Examples:

    scrappy-continuity backup --out ~/.itsmay/backups/scrappy.enc
    scrappy-continuity backup --conversation-export ~/Downloads/conversations.json
    scrappy-continuity inspect-export ~/Downloads/conversations.json
    scrappy-continuity verify-encrypted ~/.itsmay/backups/scrappy.enc
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from core.vault.bundle import VaultBundle
from core.vault.continuity import (
    CapsuleSpec,
    ContinuityRecord,
    load_bootstrap_context,
    verify_capsule,
)
from core.vault.continuity_crypto import (
    ContinuityCryptoUnavailable,
    InvalidEncryptedCapsule,
    decrypt_private_capsule,
    inspect_encrypted_header,
    write_encrypted_private_capsule,
)
from core.vault.conversation_ingest import (
    UnsupportedConversationExport,
    ingest_conversation_export,
)
from core.vault.transport import extract_vault_archive

load_dotenv()
load_dotenv(Path.home() / ".itsmay" / "config.env")

API_BASE = os.environ.get("VAULT_API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.environ.get("VAULT_API_KEY", "")
DEFAULT_SPEC = Path.home() / ".itsmay" / "continuity_spec.json"
DEFAULT_BACKUP_DIR = Path.home() / ".itsmay" / "backups"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}


def _passphrase(*, confirm: bool) -> str:
    first = getpass.getpass("Continuity passphrase (hidden): ")
    if len(first) < 12:
        raise ValueError("passphrase must be at least 12 characters")
    if confirm:
        second = getpass.getpass("Confirm passphrase: ")
        if first != second:
            raise ValueError("passphrases do not match")
    return first


def _download_vault_archive(target: Path, *, include_episodes: bool) -> Path:
    """Download one authenticated ephemeral tar from the live Vault API."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=_headers(), timeout=300, follow_redirects=True) as client:
        with client.stream(
            "POST",
            f"{API_BASE}/v1/vault/export/archive",
            json={"include_episodes": include_episodes},
        ) as response:
            if response.status_code == 401:
                raise RuntimeError("401 Unauthorized — check VAULT_API_KEY")
            if response.status_code == 409:
                try:
                    detail = response.json().get("detail", response.text)
                except Exception:
                    detail = response.text
                raise RuntimeError(f"Vault export blocked: {detail}")
            response.raise_for_status()
            with target.open("wb") as fh:
                for chunk in response.iter_bytes(1024 * 1024):
                    fh.write(chunk)
    return target


def _default_spec(bundle: VaultBundle) -> CapsuleSpec:
    identity = bundle.identity
    rules = [*identity.invariants, *[d.content for d in bundle.directives if d.active]]
    lines = ["# Scrappy Continuity Constitution", ""]
    lines.extend(f"- {rule}" for rule in rules)
    if not rules:
        lines.append("- Preserve explicit provenance and never fabricate continuity.")
    return CapsuleSpec(constitution="\n".join(lines))


def _load_spec(path: Path | None, bundle: VaultBundle) -> CapsuleSpec:
    if path is None:
        path = DEFAULT_SPEC if DEFAULT_SPEC.is_file() else None
    if path is None:
        return _default_spec(bundle)

    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    decisions = [ContinuityRecord.model_validate(item) for item in raw.get("decisions", [])]
    constitution = str(raw.get("constitution") or "").strip()
    if not constitution:
        constitution = _default_spec(bundle).constitution
    return CapsuleSpec(
        constitution=constitution,
        project_state=dict(raw.get("project_state") or {}),
        vocabulary=dict(raw.get("vocabulary") or {}),
        unresolved_threads=list(raw.get("unresolved_threads") or []),
        decisions=decisions,
        evals=list(raw.get("evals") or []),
    )


def _default_output() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_BACKUP_DIR / f"scrappy-continuity-{stamp}.enc"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _backup(args: argparse.Namespace) -> int:
    output = Path(args.out).expanduser() if args.out else _default_output()
    output.parent.mkdir(parents=True, exist_ok=True)
    spec_path = Path(args.spec).expanduser() if args.spec else None
    conversation_report = None
    if args.conversation_export:
        source = Path(args.conversation_export).expanduser()
        print(f"Normalizing private conversation export locally: {source} …")
        conversation_report = ingest_conversation_export(source)
        summary = conversation_report.summary()
        print(
            f"  {summary['conversations']} conversations · {summary['turns']} turns · "
            f"{summary['redacted_turns']} credential-shaped turn(s) masked"
        )

    print(f"Downloading live Vault from {API_BASE} …")
    with tempfile.TemporaryDirectory(prefix="scrappy-backup-") as tmp:
        tmp_root = Path(tmp)
        archive = _download_vault_archive(
            tmp_root / "vault.tar",
            include_episodes=not args.no_episodes,
        )
        bundle_dir = extract_vault_archive(archive, tmp_root / "unpacked")
        bundle = VaultBundle.read(bundle_dir)
        spec = _load_spec(spec_path, bundle)
        passphrase = _passphrase(confirm=True)
        try:
            write_encrypted_private_capsule(
                bundle,
                output,
                spec=spec,
                passphrase=passphrase,
                conversation_report=conversation_report,
            )
        finally:
            passphrase = ""  # best-effort reference release; Python strings are immutable

    digest = _sha256(output)
    size = output.stat().st_size
    print(f"✓ encrypted continuity backup: {output}")
    print(f"  bytes: {size:,}")
    print(f"  sha256: {digest}")
    print("  passphrase and conversation export stayed local; temp plaintext was removed.")
    return 0


def _inspect_export(args: argparse.Namespace) -> int:
    """Parse an export and show counts only; never print conversation text."""
    report = ingest_conversation_export(Path(args.path).expanduser())
    print(json.dumps(report.summary(), indent=2, ensure_ascii=False))
    return 0


def _verify_encrypted(args: argparse.Namespace) -> int:
    source = Path(args.path).expanduser()
    passphrase = _passphrase(confirm=False)
    try:
        with tempfile.TemporaryDirectory(prefix="scrappy-verify-") as tmp:
            root = decrypt_private_capsule(source, Path(tmp), passphrase=passphrase)
            capsule = root / "scrappy-continuity"
            report = verify_capsule(capsule)
    finally:
        passphrase = ""
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.ok else 2


def _decrypt(args: argparse.Namespace) -> int:
    source = Path(args.path).expanduser()
    destination = Path(args.out).expanduser()
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError(f"refusing to extract into non-empty directory: {destination}")
    passphrase = _passphrase(confirm=False)
    try:
        decrypt_private_capsule(source, destination, passphrase=passphrase)
    finally:
        passphrase = ""
    print(f"✓ decrypted to {destination}")
    print("WARNING: this directory contains private plaintext continuity data.")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    header = inspect_encrypted_header(Path(args.path).expanduser())
    print(json.dumps(header, indent=2, sort_keys=True))
    return 0


def _verify_dir(args: argparse.Namespace) -> int:
    report = verify_capsule(Path(args.path).expanduser())
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.ok else 2


def _bootstrap(args: argparse.Namespace) -> int:
    text = load_bootstrap_context(Path(args.path).expanduser())
    print(text)
    return 0


def _make_spec(args: argparse.Namespace) -> int:
    target = Path(args.out).expanduser() if args.out else DEFAULT_SPEC
    if target.exists() and not args.force:
        raise RuntimeError(f"{target} already exists; use --force to replace it")
    target.parent.mkdir(parents=True, exist_ok=True)
    template: dict[str, Any] = {
        "constitution": (
            "# Scrappy Continuity Constitution\n\n"
            "- Never fake the magic. Build the mechanism until reality feels magical.\n"
            "- The provider may host a model. It must never own the continuity."
        ),
        "project_state": {},
        "vocabulary": {
            "🧬": "identity / evolution",
            "🕳️": "continuity / unknown / self-reference",
            "⚙️": "execution / reality",
        },
        "unresolved_threads": [],
        "decisions": [],
        "evals": [],
    }
    target.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    print(f"✓ local continuity spec created: {target}")
    print("  Keep personal/relationship context here or in another private path — not public Git.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrappy-continuity",
        description="Provider-independent encrypted continuity backup and recovery.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="download live Vault and encrypt it locally")
    backup.add_argument("--out", help="encrypted output path")
    backup.add_argument("--spec", help="private continuity_spec.json path")
    backup.add_argument(
        "--conversation-export",
        help="local ChatGPT conversations.json to normalize into the encrypted capsule",
    )
    backup.add_argument("--no-episodes", action="store_true", help="omit raw Vault episodes")
    backup.set_defaults(func=_backup)

    export = sub.add_parser("inspect-export", help="show local conversation-export counts only")
    export.add_argument("path")
    export.set_defaults(func=_inspect_export)

    verify_enc = sub.add_parser("verify-encrypted", help="decrypt ephemerally and verify hashes")
    verify_enc.add_argument("path")
    verify_enc.set_defaults(func=_verify_encrypted)

    inspect = sub.add_parser("inspect", help="show non-secret encrypted envelope metadata")
    inspect.add_argument("path")
    inspect.set_defaults(func=_inspect)

    decrypt = sub.add_parser("decrypt", help="explicitly extract private plaintext")
    decrypt.add_argument("path")
    decrypt.add_argument("--out", required=True)
    decrypt.set_defaults(func=_decrypt)

    verify_dir = sub.add_parser("verify-dir", help="verify a plaintext capsule directory")
    verify_dir.add_argument("path")
    verify_dir.set_defaults(func=_verify_dir)

    bootstrap = sub.add_parser("bootstrap", help="print verified provider-neutral bootstrap text")
    bootstrap.add_argument("path")
    bootstrap.set_defaults(func=_bootstrap)

    spec = sub.add_parser("make-spec", help="create a private local continuity spec template")
    spec.add_argument("--out")
    spec.add_argument("--force", action="store_true")
    spec.set_defaults(func=_make_spec)
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        code = int(args.func(args) or 0)
    except (
        ContinuityCryptoUnavailable,
        InvalidEncryptedCapsule,
        UnsupportedConversationExport,
        ValueError,
        RuntimeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 2
    except httpx.HTTPStatusError as exc:
        print(f"error: HTTP {exc.response.status_code}: {exc.response.text[:300]}", file=sys.stderr)
        code = 2
    except httpx.HTTPError as exc:
        print(f"error: cannot reach {API_BASE}: {exc}", file=sys.stderr)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
