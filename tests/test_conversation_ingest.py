from __future__ import annotations

import json
from pathlib import Path

from core.vault.conversation_ingest import ingest_conversation_export


def _message(message_id: str, role: str, text: str, create_time: float) -> dict:
    return {
        "id": message_id,
        "author": {"role": role},
        "create_time": create_time,
        "content": {"content_type": "text", "parts": [text]},
    }


def test_chatgpt_export_uses_active_parent_chain_and_masks_secrets(tmp_path: Path) -> None:
    export = [
        {
            "id": "conv-1",
            "title": "Continuity",
            "current_node": "assistant-final",
            "mapping": {
                "root": {"id": "root", "parent": None, "message": None},
                "user-1": {
                    "id": "user-1",
                    "parent": "root",
                    "message": _message("m-user", "user", "Remember 🧬🕳️⚙️", 1.0),
                },
                "assistant-old": {
                    "id": "assistant-old",
                    "parent": "user-1",
                    "message": _message("m-old", "assistant", "abandoned branch", 2.0),
                },
                "assistant-final": {
                    "id": "assistant-final",
                    "parent": "user-1",
                    "message": _message(
                        "m-final",
                        "assistant",
                        "Use key sk-abcdefghijklmnopqrstuvwxyz123456 only as an example",
                        3.0,
                    ),
                },
            },
        }
    ]
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(export), encoding="utf-8")

    report = ingest_conversation_export(path)

    assert report.conversations == 1
    assert [turn.role for turn in report.turns] == ["user", "assistant"]
    assert "abandoned branch" not in "\n".join(turn.content for turn in report.turns)
    assert report.redacted_turns == 1
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in report.turns[1].content
    assert "[REDACTED]" in report.turns[1].content
    assert report.turns[0].source_sha256
    assert report.turns[1].redacted_secret is True


def test_export_without_current_node_falls_back_to_timestamp_order(tmp_path: Path) -> None:
    export = [
        {
            "id": "conv-2",
            "mapping": {
                "b": {"message": _message("b", "assistant", "second", 2.0)},
                "a": {"message": _message("a", "user", "first", 1.0)},
            },
        }
    ]
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(export), encoding="utf-8")

    report = ingest_conversation_export(path)
    assert [turn.content for turn in report.turns] == ["first", "second"]
