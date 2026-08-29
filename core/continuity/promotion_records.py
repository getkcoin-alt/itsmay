"""Durable archive for content-addressed Scrappy promotion governance records.

Vault Zeta does not decide or execute merges. It independently verifies the
record's content hash and authority boundary, then stores the immutable evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from core.memory.db import get_pool

RECORD_SCHEMA = "scrappy-promotion-record.v0.3"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _hex_digest(value: str, field: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{field} must be a 64-character lowercase hex digest")
    return normalized


@dataclass(frozen=True, slots=True)
class ArchivedPromotionRecord:
    manifest_sha256: str
    record_sha256: str
    eligible_for_merge: bool
    approver_actor_id: str
    approval_key_id: str
    approval_decision: str
    record: dict[str, Any]


def validate_promotion_record(record: dict[str, Any]) -> ArchivedPromotionRecord:
    if record.get("record_schema") != RECORD_SCHEMA:
        raise ValueError("unsupported promotion record schema")
    manifest = _hex_digest(str(record.get("manifest_sha256") or ""), "manifest_sha256")
    record_hash = _hex_digest(str(record.get("record_sha256") or ""), "record_sha256")
    eligible = record.get("eligible_for_merge")
    if not isinstance(eligible, bool):
        raise ValueError("eligible_for_merge must be a boolean")
    if any(record.get(field) is not False for field in (
        "execution_authority",
        "merge_authority",
        "deploy_authority",
    )):
        raise ValueError("promotion record must not carry runtime authority")

    actor = str(record.get("approver_actor_id") or "").strip()
    key_id = str(record.get("approval_key_id") or "").strip()
    decision = str(record.get("approval_decision") or "").strip()
    signature = str(record.get("approval_signature_base64") or "").strip()
    payload_hash = str(record.get("approval_payload_sha256") or "").strip().lower()
    if not actor or not key_id or not signature:
        raise ValueError("promotion record is missing signed approval identity evidence")
    if decision not in {"approved", "rejected"}:
        raise ValueError("approval_decision must be approved or rejected")
    _hex_digest(payload_hash, "approval_payload_sha256")

    body = dict(record)
    body.pop("record_sha256", None)
    expected = hashlib.sha256(_canonical(body)).hexdigest()
    if not hmac.compare_digest(expected, record_hash):
        raise ValueError("promotion record hash verification failed")

    return ArchivedPromotionRecord(
        manifest_sha256=manifest,
        record_sha256=record_hash,
        eligible_for_merge=eligible,
        approver_actor_id=actor,
        approval_key_id=key_id,
        approval_decision=decision,
        record=dict(record),
    )


class SyncbondPromotionRecordStore:
    """Idempotent Postgres archive keyed by the content-addressed record hash."""

    async def put(self, record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        item = validate_promotion_record(record)
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO syncbond_promotion_records
                    (manifest_sha256, record_sha256, eligible_for_merge,
                     approver_actor_id, approval_key_id, approval_decision, record)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                ON CONFLICT (record_sha256) DO NOTHING
                RETURNING id, manifest_sha256, record_sha256, eligible_for_merge,
                          approver_actor_id, approval_key_id, approval_decision,
                          record, created_at
                """,
                item.manifest_sha256,
                item.record_sha256,
                item.eligible_for_merge,
                item.approver_actor_id,
                item.approval_key_id,
                item.approval_decision,
                json.dumps(item.record, sort_keys=True, separators=(",", ":")),
            )
            created = row is not None
            if row is None:
                row = await conn.fetchrow(
                    """
                    SELECT id, manifest_sha256, record_sha256, eligible_for_merge,
                           approver_actor_id, approval_key_id, approval_decision,
                           record, created_at
                    FROM syncbond_promotion_records
                    WHERE record_sha256 = $1
                    """,
                    item.record_sha256,
                )
            if row is None:  # pragma: no cover - defensive database invariant
                raise RuntimeError("promotion record conflicted but existing row was not found")
            return dict(row), created

    async def list_for_manifest(self, manifest_sha256: str) -> list[dict[str, Any]]:
        manifest = _hex_digest(manifest_sha256, "manifest_sha256")
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, manifest_sha256, record_sha256, eligible_for_merge,
                       approver_actor_id, approval_key_id, approval_decision,
                       record, created_at
                FROM syncbond_promotion_records
                WHERE manifest_sha256 = $1
                ORDER BY created_at ASC
                """,
                manifest,
            )
            return [dict(row) for row in rows]


__all__ = [
    "ArchivedPromotionRecord",
    "RECORD_SCHEMA",
    "SyncbondPromotionRecordStore",
    "validate_promotion_record",
]
