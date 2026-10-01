"""Deterministic serialization for signed authority artifacts."""

from __future__ import annotations

import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Return a stable UTF-8 representation suitable for signatures.

    This is deliberately a narrow canonical form for our own typed payloads,
    not a claim of full RFC 8785 compatibility.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
