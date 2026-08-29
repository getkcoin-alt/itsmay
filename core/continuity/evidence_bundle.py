"""Portable, content-addressed handoff from Vault Zeta to Scrappy Forge.

The bundle contains only the already-distilled SYNCBOND Experience envelope.
It deliberately excludes database ids, credentials, raw machine logs and private
memory records. Forge can validate this document without connecting to Vault's
database or Scrappy OS.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from core.continuity.experiences import DistilledExperience
from core.contracts.syncbond import EventType, SYNCBOND_VERSION

BUNDLE_FORMAT = "syncbond.experience-evidence.v1"


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def export_experience_bundle(item: DistilledExperience) -> dict[str, Any]:
    """Return a deterministic, transport-safe evidence bundle."""

    envelope = item.envelope.model_dump(mode="json")
    if envelope.get("event_type") != EventType.EXPERIENCE_RECORDED.value:
        raise ValueError("evidence bundle requires an experience.recorded envelope")
    if str(envelope.get("correlation_id")) != str(item.correlation_id):
        raise ValueError("experience envelope correlation_id does not match distilled item")
    payload = envelope.get("payload") or {}
    if str(payload.get("objective_id")) != str(item.remote_objective_id):
        raise ValueError("experience objective_id does not match distilled item")

    body: dict[str, Any] = {
        "bundle_format": BUNDLE_FORMAT,
        "protocol": "SYNCBOND",
        "schema_version": SYNCBOND_VERSION,
        "correlation_id": str(item.correlation_id),
        "remote_objective_id": str(item.remote_objective_id),
        "envelope": envelope,
    }
    body["bundle_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def verify_bundle_hash(bundle: dict[str, Any]) -> bool:
    """Verify transport integrity without trusting the claimed hash."""

    claimed = bundle.get("bundle_sha256")
    if not isinstance(claimed, str) or len(claimed) != 64:
        return False
    body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    actual = hashlib.sha256(_canonical(body)).hexdigest()
    return hmac.compare_digest(actual, claimed)


__all__ = ["BUNDLE_FORMAT", "export_experience_bundle", "verify_bundle_hash"]
