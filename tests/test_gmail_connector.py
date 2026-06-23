"""Tests for the Gmail connector — mocks out the Google API client."""

from __future__ import annotations

import base64
import json
from email import message_from_bytes
from unittest.mock import MagicMock, patch

import pytest

from core.connectors.base import InvocationContext
from core.connectors.gmail.connector import GmailConnector, _build_mime, _extract_body

# ── helpers ─────────────────────────────────────────────────────────────────


def _make_connector() -> tuple[GmailConnector, MagicMock]:
    """Return a GmailConnector whose Google service is replaced with a MagicMock."""
    creds_json = json.dumps(
        {
            "token": "tok",
            "refresh_token": "ref",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "cid",
            "client_secret": "cs",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        }
    )
    mock_service = MagicMock()
    with (
        patch("core.connectors.gmail.connector._HAS_GOOGLE", True),
        patch("core.connectors.gmail.connector.build", return_value=mock_service),
        patch(
            "core.connectors.gmail.auth.load_credentials",
            return_value=MagicMock(valid=True),
        ),
        patch("core.config.get_settings") as mock_settings,
    ):
        mock_settings.return_value.google_credentials_json = creds_json
        conn = GmailConnector()
    conn._service = mock_service
    return conn, mock_service


def _header(name: str, value: str) -> dict:
    return {"name": name, "value": value}


def _fake_thread_list(thread_ids: list[str]) -> dict:
    return {"threads": [{"id": tid} for tid in thread_ids]}


def _fake_thread_metadata(thread_id: str, subject: str, sender: str, date: str) -> dict:
    return {
        "id": thread_id,
        "snippet": f"snippet of {subject}",
        "messages": [
            {
                "id": f"msg_{thread_id}",
                "payload": {
                    "headers": [
                        _header("Subject", subject),
                        _header("From", sender),
                        _header("Date", date),
                    ]
                },
            }
        ],
    }


def _encode_body(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _fake_full_thread(thread_id: str, body_text: str) -> dict:
    return {
        "id": thread_id,
        "messages": [
            {
                "id": f"msg_{thread_id}",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        _header("Subject", "Test Subject"),
                        _header("From", "sender@example.com"),
                        _header("To", "karnveer@example.com"),
                        _header("Date", "Mon, 1 Jan 2024 10:00:00 +0000"),
                    ],
                    "body": {"data": _encode_body(body_text)},
                    "parts": [],
                },
            }
        ],
    }


# ── unit tests ───────────────────────────────────────────────────────────────


def test_connector_skipped_when_no_credentials():
    with (
        patch("core.connectors.gmail.connector._HAS_GOOGLE", True),
        patch("core.connectors.gmail.connector.build"),
        patch("core.config.get_settings") as mock_settings,
    ):
        mock_settings.return_value.google_credentials_json = ""
        with pytest.raises(ValueError, match="GOOGLE_CREDENTIALS_JSON"):
            GmailConnector()


def test_connector_skipped_when_library_missing():
    with patch("core.connectors.gmail.connector._HAS_GOOGLE", False):
        with pytest.raises(RuntimeError, match="google-api-python-client"):
            GmailConnector()


async def test_search_returns_thread_summaries():
    conn, svc = _make_connector()
    thread_ids = ["tid1", "tid2"]

    svc.users().threads().list().execute.return_value = _fake_thread_list(thread_ids)
    svc.users().threads().get().execute.side_effect = [
        _fake_thread_metadata("tid1", "Invoice April", "stripe@stripe.com", "2024-04-01"),
        _fake_thread_metadata("tid2", "Invoice May", "stripe@stripe.com", "2024-05-01"),
    ]

    results = await conn.invoke(
        "search", {"query": "from:stripe.com subject:invoice"}, InvocationContext()
    )
    assert len(results) == 2
    assert results[0]["thread_id"] == "tid1"
    assert results[0]["subject"] == "Invoice April"
    assert results[0]["from"] == "stripe@stripe.com"


async def test_search_returns_empty_list_when_no_results():
    conn, svc = _make_connector()
    svc.users().threads().list().execute.return_value = {"threads": []}

    results = await conn.invoke("search", {"query": "nothing here"}, InvocationContext())
    assert results == []


async def test_read_thread_decodes_body():
    conn, svc = _make_connector()
    body_text = "Hello Karnveer, your invoice is ready."
    svc.users().threads().get().execute.return_value = _fake_full_thread("tid1", body_text)

    messages = await conn.invoke("read_thread", {"thread_id": "tid1"}, InvocationContext())
    assert len(messages) == 1
    assert body_text in messages[0]["body"]
    assert messages[0]["from"] == "sender@example.com"
    assert messages[0]["subject"] == "Test Subject"


async def test_draft_creates_draft_and_returns_id():
    conn, svc = _make_connector()
    svc.users().drafts().create().execute.return_value = {"id": "draft_xyz"}

    result = await conn.invoke(
        "draft",
        {"to": "boss@acme.com", "subject": "Re: Project", "body": "Got it, working on it."},
        InvocationContext(),
    )
    assert result["draft_id"] == "draft_xyz"
    assert result["status"] == "draft created"


async def test_draft_sets_thread_id_for_reply():
    conn, svc = _make_connector()
    svc.users().drafts().create().execute.return_value = {"id": "draft_abc"}

    await conn.invoke(
        "draft",
        {
            "to": "boss@acme.com",
            "subject": "Re: Project",
            "body": "On it.",
            "reply_to_thread_id": "tid1",
        },
        InvocationContext(),
    )
    call_kwargs = svc.users().drafts().create.call_args
    body_arg = (
        call_kwargs[1].get("body") or call_kwargs[0][0]
        if call_kwargs[0]
        else call_kwargs[1]["body"]
    )
    assert body_arg["message"]["threadId"] == "tid1"


async def test_send_returns_message_id():
    conn, svc = _make_connector()
    svc.users().messages().send().execute.return_value = {"id": "msg_sent_123"}

    result = await conn.invoke(
        "send",
        {"to": "friend@example.com", "subject": "Hey", "body": "What's up?"},
        InvocationContext(),
    )
    assert result["message_id"] == "msg_sent_123"
    assert result["status"] == "sent"


def test_send_tool_has_requires_approval():
    send_tool = next(t for t in GmailConnector.manifest.tools if t.name == "send")
    assert send_tool.requires_approval is True


def test_build_mime_encodes_correctly():
    raw = _build_mime({"to": "a@b.com", "subject": "Test", "body": "Hello"})
    parsed = message_from_bytes(raw)
    assert parsed["To"] == "a@b.com"
    assert parsed["Subject"] == "Test"


def test_extract_body_text_plain():
    body_text = "Simple plain-text body."
    payload = {
        "mimeType": "text/plain",
        "body": {"data": _encode_body(body_text)},
        "parts": [],
    }
    result = _extract_body(payload)
    assert result
    assert body_text in result


def test_extract_body_nested_parts():
    body_text = "Nested body text."
    payload = {
        "mimeType": "multipart/mixed",
        "body": {},
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": _encode_body(body_text)},
                "parts": [],
            }
        ],
    }
    assert _extract_body(payload) == body_text
