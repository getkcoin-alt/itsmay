"""Local-only ingestion of conversation exports into private continuity capsules.

The first supported source is ChatGPT's ``conversations.json`` style export. The
parser is intentionally tolerant of missing/extra fields and reconstructs the
active parent chain when ``current_node`` is available, so abandoned branches
are not silently mixed into the primary conversation history.

This module does not call any model or network service. It normalizes explicit
exported messages, hashes their source objects for provenance, and masks narrow
credential-shaped values before they enter the continuity capsule.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from core.vault.redact import find_secret, mask


class UnsupportedConversationExport(ValueError):
    """The supplied file is not a supported conversation-export structure."""


class ConversationTurn(BaseModel):
    id: str
    conversation_id: str
    title: str = ""
    role: str
    content: str
    created_at: datetime | None = None
    source: str = "chatgpt-export"
    source_node_id: str = ""
    source_sha256: str
    redacted_secret: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class ConversationIngestReport:
    source_path: str
    conversations: int = 0
    turns: list[ConversationTurn] = field(default_factory=list)
    redacted_turns: int = 0
    skipped_empty: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "conversations": self.conversations,
            "turns": len(self.turns),
            "redacted_turns": self.redacted_turns,
            "skipped_empty": self.skipped_empty,
            "warnings": self.warnings,
        }


def ingest_conversation_export(path: Path) -> ConversationIngestReport:
    """Parse a supported JSON conversation export entirely on the local host."""
    path = Path(path).expanduser()
    if path.suffix.lower() != ".json":
        raise UnsupportedConversationExport(
            "only JSON conversation exports are supported in this first slice"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UnsupportedConversationExport(f"cannot read JSON export: {exc}") from exc

    conversations = _conversation_list(raw)
    report = ConversationIngestReport(source_path=str(path), conversations=len(conversations))
    for index, conversation in enumerate(conversations):
        if not isinstance(conversation, dict):
            report.warnings.append(f"conversation[{index}] ignored: expected object")
            continue
        _ingest_chatgpt_conversation(conversation, report, fallback_id=f"conversation-{index}")
    return report


def augment_private_capsule(directory: Path, report: ConversationIngestReport) -> None:
    """Add normalized turns to an encrypted-private staging capsule.

    The caller must invoke this only before encryption. Files are added to the
    capsule's SHA-256 manifest as well as the outer AES-GCM envelope.
    """
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    checksum_path = directory / "provenance" / "checksums.json"
    if not manifest_path.is_file() or not checksum_path.is_file():
        raise ValueError("not a continuity capsule staging directory")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("privacy") != "encrypted-private":
        raise ValueError("conversation exports may only augment encrypted-private capsules")

    conversation_dir = directory / "conversation"
    conversation_dir.mkdir(parents=True, exist_ok=True)
    turns_path = conversation_dir / "turns.jsonl"
    sources_path = directory / "provenance" / "conversation_sources.jsonl"

    with turns_path.open("w", encoding="utf-8") as turns_fh, sources_path.open(
        "w", encoding="utf-8"
    ) as source_fh:
        for turn in report.turns:
            payload = turn.model_dump(mode="json")
            turns_fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            source_fh.write(
                json.dumps(
                    {
                        "turn_id": turn.id,
                        "conversation_id": turn.conversation_id,
                        "source": turn.source,
                        "source_node_id": turn.source_node_id,
                        "source_sha256": turn.source_sha256,
                        "redacted_secret": turn.redacted_secret,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )

    counts = dict(manifest.get("counts") or {})
    counts["external_conversation_turns"] = len(report.turns)
    counts["external_conversations"] = report.conversations
    counts["redacted_conversation_turns"] = report.redacted_turns
    manifest["counts"] = counts
    manifest["conversation_ingest"] = {
        "source_format": "chatgpt-export-json",
        "redaction": "credential-shape-mask-v1",
        "warnings": report.warnings,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    checksums = json.loads(checksum_path.read_text(encoding="utf-8"))
    for path in (manifest_path, turns_path, sources_path):
        rel = path.relative_to(directory).as_posix()
        checksums[rel] = _sha256_file(path)
    checksum_path.write_text(
        json.dumps(checksums, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _conversation_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("conversations", "items"):
            value = raw.get(key)
            if isinstance(value, list):
                return value
    raise UnsupportedConversationExport(
        "expected a list of conversations or an object containing a conversations list"
    )


def _ingest_chatgpt_conversation(
    conversation: dict[str, Any],
    report: ConversationIngestReport,
    *,
    fallback_id: str,
) -> None:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        report.warnings.append(f"{fallback_id}: no ChatGPT-style mapping; ignored")
        return

    conversation_id = str(conversation.get("id") or conversation.get("conversation_id") or fallback_id)
    title = str(conversation.get("title") or "")
    node_ids = _active_node_ids(mapping, conversation.get("current_node"))
    for node_id in node_ids:
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        role = _role(message)
        content = _message_text(message)
        if not content.strip():
            report.skipped_empty += 1
            continue

        secret_kind = find_secret(content)
        redacted = secret_kind is not None
        if redacted:
            content = mask(content)
            report.redacted_turns += 1

        source_hash = _sha256_json(message)
        turn_id = str(message.get("id") or node_id)
        report.turns.append(
            ConversationTurn(
                id=f"{conversation_id}:{turn_id}",
                conversation_id=conversation_id,
                title=title,
                role=role,
                content=content,
                created_at=_timestamp(message.get("create_time")),
                source_node_id=str(node_id),
                source_sha256=source_hash,
                redacted_secret=redacted,
                metadata={
                    "content_type": _content_type(message),
                    "recipient": message.get("recipient"),
                    "secret_kind": secret_kind if redacted else None,
                },
            )
        )


def _active_node_ids(mapping: dict[str, Any], current_node: Any) -> list[str]:
    """Return one active root→leaf chain, falling back to timestamp order."""
    current = str(current_node or "")
    if current and current in mapping:
        reversed_ids: list[str] = []
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            reversed_ids.append(current)
            node = mapping.get(current)
            if not isinstance(node, dict):
                break
            parent = node.get("parent")
            current = str(parent) if parent else ""
        return list(reversed(reversed_ids))

    candidates: list[tuple[float, str]] = []
    for node_id, node in mapping.items():
        if not isinstance(node, dict) or not isinstance(node.get("message"), dict):
            continue
        raw_time = node["message"].get("create_time")
        try:
            stamp = float(raw_time) if raw_time is not None else 0.0
        except (TypeError, ValueError):
            stamp = 0.0
        candidates.append((stamp, str(node_id)))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [node_id for _, node_id in candidates]


def _role(message: dict[str, Any]) -> str:
    author = message.get("author")
    if isinstance(author, dict):
        role = author.get("role")
        if role:
            return str(role)
    return "unknown"


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if isinstance(parts, list):
        rendered = [_render_part(part) for part in parts]
        return "\n".join(part for part in rendered if part)
    text = content.get("text")
    return str(text) if isinstance(text, str) else ""


def _render_part(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        for key in ("text", "content", "caption"):
            value = part.get(key)
            if isinstance(value, str):
                return value
    return ""


def _content_type(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        return str(content.get("content_type") or "")
    return ""


def _timestamp(raw: Any) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
