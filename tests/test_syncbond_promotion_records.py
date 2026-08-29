from __future__ import annotations

import hashlib
import json

import pytest

from core.continuity.promotion_records import validate_promotion_record


def _record() -> dict:
    body = {
        "record_schema": "scrappy-promotion-record.v0.3",
        "manifest_sha256": "a" * 64,
        "eligible_for_merge": True,
        "security_evidence_ref": "ci://security/123",
        "regression_evidence_ref": "ci://regression/456",
        "approval_payload_sha256": "b" * 64,
        "approval_signature_base64": "ZmFrZS1zaWduYXR1cmU=",
        "approval_key_id": "command-center-primary",
        "approver_actor_id": "human:karnveer",
        "approval_decision": "approved",
        "reasons": ["all evidence-bound promotion gates passed"],
        "execution_authority": False,
        "merge_authority": False,
        "deploy_authority": False,
    }
    encoded = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return {**body, "record_sha256": hashlib.sha256(encoded).hexdigest()}


def test_valid_record_is_archivable_but_remains_inert():
    item = validate_promotion_record(_record())

    assert item.manifest_sha256 == "a" * 64
    assert item.eligible_for_merge is True
    assert item.approver_actor_id == "human:karnveer"
    assert item.approval_key_id == "command-center-primary"
    assert item.record["merge_authority"] is False
    assert item.record["deploy_authority"] is False


def test_tampered_record_hash_fails_closed():
    record = _record()
    record["eligible_for_merge"] = False

    with pytest.raises(ValueError, match="hash verification failed"):
        validate_promotion_record(record)


def test_record_claiming_merge_authority_is_rejected_even_if_rehashed():
    record = _record()
    record["merge_authority"] = True
    body = dict(record)
    body.pop("record_sha256")
    record["record_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    with pytest.raises(ValueError, match="must not carry runtime authority"):
        validate_promotion_record(record)


def test_record_requires_signed_approval_identity_evidence():
    record = _record()
    record["approval_signature_base64"] = ""
    body = dict(record)
    body.pop("record_sha256")
    record["record_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    with pytest.raises(ValueError, match="missing signed approval identity evidence"):
        validate_promotion_record(record)


def test_unknown_schema_is_rejected():
    record = _record()
    record["record_schema"] = "future-schema"

    with pytest.raises(ValueError, match="unsupported promotion record schema"):
        validate_promotion_record(record)
