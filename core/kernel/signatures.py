"""Signature verification for authority artifacts.

Private signing keys intentionally do not belong in the agent kernel. The
kernel receives trusted public keys and verifies externally issued artifacts.
"""

from __future__ import annotations

import base64


class SignatureBackendUnavailable(RuntimeError):
    """Raised when Ed25519 support is not installed in the current runtime."""


def verify_ed25519(*, public_key_b64: str, signature_b64: str, message: bytes) -> bool:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SignatureBackendUnavailable(
            'authority verification requires the "cryptography" package'
        ) from exc

    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_b64, validate=True)
        )
        signature = base64.b64decode(signature_b64, validate=True)
        public_key.verify(signature, message)
    except (ValueError, InvalidSignature):
        return False
    return True
