"""Encryption wrapper for private Scrappy continuity capsules.

Format v1 is a streaming AES-256-GCM envelope around a tar archive. The key is
derived from an operator passphrase with scrypt. The passphrase/key is never
written into the archive or manifest.

The plaintext capsule exists only inside a temporary directory while packaging
and is removed before this function returns. Callers still need normal host disk
security because no user-space process can promise that temporary plaintext was
never paged or journaled by the operating system.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import tarfile
import tempfile
from pathlib import Path
from typing import BinaryIO

from core.vault.bundle import VaultBundle
from core.vault.continuity import CapsuleSpec, write_private_staging_capsule
from core.vault.conversation_ingest import ConversationIngestReport, augment_private_capsule

_MAGIC = b"SCRAPPY-CONTINUITY-ENC1\n"
_TAG_BYTES = 16
_CHUNK = 1024 * 1024
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1


class ContinuityCryptoUnavailable(RuntimeError):
    """Install the continuity optional dependency to encrypt/decrypt capsules."""


class InvalidEncryptedCapsule(ValueError):
    """The encrypted file is malformed, truncated, or failed authentication."""


def _crypto():
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ContinuityCryptoUnavailable(
            'encrypted continuity requires `pip install ".[continuity]"`'
        ) from exc
    return hashes, Cipher, algorithms, modes, Scrypt


def write_encrypted_private_capsule(
    bundle: VaultBundle,
    output_path: Path,
    *,
    spec: CapsuleSpec,
    passphrase: str,
    conversation_report: ConversationIngestReport | None = None,
) -> Path:
    """Build a private capsule and write one authenticated encrypted file.

    ``conversation_report`` is already-normalized local export data. When
    supplied it is added only to the ephemeral private staging directory and is
    therefore covered by both per-file SHA-256 and the outer AES-GCM envelope.
    """
    if len(passphrase) < 12:
        raise ValueError("continuity passphrase must be at least 12 characters")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="scrappy-continuity-") as tmp:
        tmp_root = Path(tmp)
        staging = tmp_root / "capsule"
        tar_path = tmp_root / "capsule.tar"

        write_private_staging_capsule(
            bundle,
            staging,
            spec=spec,
            encrypted_destination=True,
        )
        if conversation_report is not None:
            augment_private_capsule(staging, conversation_report)
        _pack_tar(staging, tar_path)
        _encrypt_file(tar_path, output_path, passphrase)

    return output_path


def decrypt_private_capsule(
    encrypted_path: Path,
    destination: Path,
    *,
    passphrase: str,
) -> Path:
    """Authenticate/decrypt a private capsule into ``destination`` safely."""
    encrypted_path = Path(encrypted_path)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="scrappy-continuity-decrypt-") as tmp:
        tar_path = Path(tmp) / "capsule.tar"
        _decrypt_file(encrypted_path, tar_path, passphrase)
        _safe_extract_tar(tar_path, destination)
    return destination


def inspect_encrypted_header(path: Path) -> dict:
    """Read non-secret envelope metadata without decrypting the capsule."""
    with Path(path).open("rb") as fh:
        magic = fh.readline()
        if magic != _MAGIC:
            raise InvalidEncryptedCapsule("not a Scrappy continuity encrypted capsule")
        header_len_raw = fh.read(4)
        if len(header_len_raw) != 4:
            raise InvalidEncryptedCapsule("truncated encrypted capsule header")
        header_len = struct.unpack(">I", header_len_raw)[0]
        if header_len <= 0 or header_len > 64 * 1024:
            raise InvalidEncryptedCapsule("invalid encrypted capsule header length")
        try:
            return json.loads(fh.read(header_len).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidEncryptedCapsule("invalid encrypted capsule header") from exc


def _pack_tar(source: Path, target: Path) -> None:
    with tarfile.open(target, "w") as tar:
        for path in sorted(source.rglob("*")):
            arcname = Path("scrappy-continuity") / path.relative_to(source)
            tar.add(path, arcname=arcname, recursive=False)


def _encrypt_file(source: Path, target: Path, passphrase: str) -> None:
    _, Cipher, algorithms, modes, Scrypt = _crypto()
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P).derive(
        passphrase.encode("utf-8")
    )
    header = {
        "format": "scrappy-continuity-enc-v1",
        "cipher": "AES-256-GCM",
        "kdf": "scrypt",
        "scrypt": {"n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P},
        "salt_b64": base64.b64encode(salt).decode("ascii"),
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
    }
    header_bytes = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")

    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    # Bind the cleartext header to authentication; changing KDF/cipher metadata
    # invalidates the GCM tag rather than silently changing interpretation.
    encryptor.authenticate_additional_data(header_bytes)

    with source.open("rb") as src, target.open("wb") as dst:
        dst.write(_MAGIC)
        dst.write(struct.pack(">I", len(header_bytes)))
        dst.write(header_bytes)
        _copy_crypt(src, dst, encryptor)
        encryptor.finalize()
        dst.write(encryptor.tag)


def _decrypt_file(source: Path, target: Path, passphrase: str) -> None:
    _, Cipher, algorithms, modes, Scrypt = _crypto()
    total = source.stat().st_size

    with source.open("rb") as src:
        if src.readline() != _MAGIC:
            raise InvalidEncryptedCapsule("not a Scrappy continuity encrypted capsule")
        raw_len = src.read(4)
        if len(raw_len) != 4:
            raise InvalidEncryptedCapsule("truncated encrypted capsule header")
        header_len = struct.unpack(">I", raw_len)[0]
        if header_len <= 0 or header_len > 64 * 1024:
            raise InvalidEncryptedCapsule("invalid encrypted capsule header length")
        header_bytes = src.read(header_len)
        if len(header_bytes) != header_len:
            raise InvalidEncryptedCapsule("truncated encrypted capsule header")
        try:
            header = json.loads(header_bytes.decode("utf-8"))
            salt = base64.b64decode(header["salt_b64"], validate=True)
            nonce = base64.b64decode(header["nonce_b64"], validate=True)
            params = header["scrypt"]
        except Exception as exc:
            raise InvalidEncryptedCapsule("invalid encrypted capsule header") from exc

        if header.get("format") != "scrappy-continuity-enc-v1":
            raise InvalidEncryptedCapsule("unsupported encrypted capsule format")
        if header.get("cipher") != "AES-256-GCM" or header.get("kdf") != "scrypt":
            raise InvalidEncryptedCapsule("unsupported encryption parameters")

        body_start = len(_MAGIC) + 4 + header_len
        cipher_len = total - body_start - _TAG_BYTES
        if cipher_len < 0:
            raise InvalidEncryptedCapsule("truncated encrypted capsule")
        src.seek(total - _TAG_BYTES)
        tag = src.read(_TAG_BYTES)
        src.seek(body_start)

        try:
            key = Scrypt(
                salt=salt,
                length=32,
                n=int(params["n"]),
                r=int(params["r"]),
                p=int(params["p"]),
            ).derive(passphrase.encode("utf-8"))
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(header_bytes)
            with target.open("wb") as dst:
                remaining = cipher_len
                while remaining:
                    block = src.read(min(_CHUNK, remaining))
                    if not block:
                        raise InvalidEncryptedCapsule("truncated encrypted capsule body")
                    dst.write(decryptor.update(block))
                    remaining -= len(block)
                dst.write(decryptor.finalize())
        except InvalidEncryptedCapsule:
            raise
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise InvalidEncryptedCapsule(
                "capsule authentication failed (wrong passphrase or modified file)"
            ) from exc


def _copy_crypt(src: BinaryIO, dst: BinaryIO, encryptor) -> None:
    while True:
        block = src.read(_CHUNK)
        if not block:
            return
        dst.write(encryptor.update(block))


def _safe_extract_tar(tar_path: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(tar_path, "r") as tar:
        members = tar.getmembers()
        for member in members:
            candidate = (root / member.name).resolve()
            if candidate != root and root not in candidate.parents:
                raise InvalidEncryptedCapsule("archive contains path traversal")
            if member.issym() or member.islnk():
                raise InvalidEncryptedCapsule("archive links are not allowed")
        tar.extractall(destination, members=members, filter="data")


def erase_directory_best_effort(path: Path) -> None:
    """Compatibility helper for callers managing their own staging directory."""
    shutil.rmtree(path, ignore_errors=True)
