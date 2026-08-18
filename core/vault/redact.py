"""Secret detection for the vault exporter.

A bundle is designed to be copied between machines, committed, and handed to
another runtime. A credential inside one is a credential published. So this runs
at the *sink* — every record on its way into a bundle — rather than trusting each
call site to have been careful.

Detection is by value shape, not by key name: a memory is free text, so there is
no "password" field to look for. If Karnveer once said "deploy it with
sk-live-abc123", that sentence is now a memory, and it must not travel.

Deliberately *fails* the export rather than silently masking. A vault that
quietly rewrote its own contents would be worse than one that refused: you would
trust a bundle that no longer says what you think it says.
"""

from __future__ import annotations

import re
from typing import Final

#: Credential shapes that are secret wherever they appear. Kept narrow on
#: purpose — a false positive blocks an export, so each pattern must be a shape
#: that is essentially never innocent prose.
SECRET_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("openai-style key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("aws access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    ),
    (
        "private key block",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ),
    ("bearer/basic header", re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._\-+/=]{20,}")),
    (
        "connection string with password",
        re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s/@]{6,}@"),
    ),
)


class SecretInVault(Exception):
    """A credential was found in content bound for a bundle. The export stops."""

    def __init__(self, kind: str, where: str) -> None:
        super().__init__(
            f"refusing to export: found what looks like a {kind} in {where}. "
            "A vault bundle is portable — secrets must stay in the host's own "
            "store and be referenced by name, never carried. Remove or redact "
            "that record, then export again."
        )
        self.kind = kind
        self.where = where


def find_secret(text: str) -> str | None:
    """Return the kind of credential found in `text`, or None if it looks clean."""
    if not text:
        return None
    for kind, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return kind
    return None


def assert_clean(text: str, where: str) -> None:
    """Raise `SecretInVault` if `text` carries something that must not travel."""
    kind = find_secret(text)
    if kind is not None:
        raise SecretInVault(kind, where)


def mask(text: str) -> str:
    """Replace credential-shaped runs with `[REDACTED]`.

    Not used on the export path (which refuses instead) — this is for log lines
    and error messages that need to quote content without leaking it.
    """
    out = text or ""
    for _kind, pattern in SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out
