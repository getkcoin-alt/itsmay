"""Gmail connector — server-side email tools.

Exposes four tools the Email expert (and Scrappy directly) can call:
  gmail.search       — search inbox by Gmail query string
  gmail.read_thread  — fetch full thread content by thread_id
  gmail.draft        — create a draft (compose without sending)
  gmail.send         — send an email immediately

Requires `GOOGLE_CREDENTIALS_JSON` env var (a JSON blob from the one-time
`scripts/gmail_auth.py` flow). If the var is unset or the google library is
missing the connector silently disappears from the toolbox — no crash.

The Google API client is synchronous, so all calls go through
`asyncio.to_thread` to keep the event loop free.
"""

from __future__ import annotations

import asyncio
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

try:
    from googleapiclient.discovery import build

    _HAS_GOOGLE = True
except ImportError:
    build = None  # type: ignore[assignment]  # placeholder so patch() can find the name
    _HAS_GOOGLE = False

from core.connectors.base import Connector, ConnectorManifest, InvocationContext, ToolSpec
from core.logging import get_logger

log = get_logger(__name__)

_GMAIL_TOOLS = [
    ToolSpec(
        name="search",
        description=(
            "Search Gmail using Gmail query syntax. Returns up to `max_results` "
            "thread summaries (thread_id, subject, sender, snippet, date). "
            "Examples: 'from:boss@acme.com', 'subject:invoice is:unread', "
            "'after:2024/01/01 from:stripe.com'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query (same syntax as the Gmail search bar).",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max threads to return (1-50, default 10).",
                },
            },
            "required": ["query"],
        },
        executor="server",
    ),
    ToolSpec(
        name="read_thread",
        description=(
            "Read the full content of an email thread by its thread_id "
            "(obtained from gmail.search). Returns all messages with sender, "
            "date, subject, and body text."
        ),
        parameters={
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "The thread_id returned by gmail.search.",
                },
            },
            "required": ["thread_id"],
        },
        executor="server",
    ),
    ToolSpec(
        name="draft",
        description=(
            "Create a Gmail draft without sending. Use for composing replies or "
            "new emails that Karnveer will review before sending. "
            "Pass `reply_to_thread_id` to make it a reply in an existing thread."
        ),
        parameters={
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address(es), comma-separated.",
                },
                "subject": {"type": "string", "description": "Email subject line."},
                "body": {"type": "string", "description": "Plain-text email body."},
                "reply_to_thread_id": {
                    "type": "string",
                    "description": "Thread ID to reply into (optional).",
                },
            },
            "required": ["to", "subject", "body"],
        },
        executor="server",
    ),
    ToolSpec(
        name="send",
        description=(
            "Send an email immediately. Only use when explicitly asked to send "
            "right now. For composing/drafting, prefer gmail.draft. "
            "Pass `reply_to_thread_id` to send as a reply."
        ),
        parameters={
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address(es), comma-separated.",
                },
                "subject": {"type": "string", "description": "Email subject line."},
                "body": {"type": "string", "description": "Plain-text email body."},
                "reply_to_thread_id": {
                    "type": "string",
                    "description": "Thread ID to reply into (optional).",
                },
            },
            "required": ["to", "subject", "body"],
        },
        executor="server",
        requires_approval=True,
        side_effects=["gmail.send"],
    ),
]


class GmailConnector(Connector):
    manifest = ConnectorManifest(
        name="gmail",
        version="0.1.0",
        description="Read, draft, and send Gmail email on Karnveer's behalf.",
        tools=_GMAIL_TOOLS,
    )

    def __init__(self) -> None:
        if not _HAS_GOOGLE:
            raise RuntimeError(
                "google-api-python-client is not installed. "
                "Add 'gmail' extras: pip install -e '.[gmail]'"
            )
        from core.config import get_settings

        creds_json = get_settings().google_credentials_json
        if not creds_json:
            raise ValueError(
                "GOOGLE_CREDENTIALS_JSON is not set — Gmail connector disabled. "
                "Run scripts/gmail_auth.py to authorize and populate the env var."
            )
        from core.connectors.gmail.auth import load_credentials

        self._creds = load_credentials(creds_json)
        self._service = build("gmail", "v1", credentials=self._creds, cache_discovery=False)
        log.info("gmail.connector_ready")

    async def invoke(self, action: str, args: dict, ctx: InvocationContext) -> Any:
        if action == "search":
            return await asyncio.to_thread(self._search, args)
        if action == "read_thread":
            return await asyncio.to_thread(self._read_thread, args)
        if action == "draft":
            return await asyncio.to_thread(self._create_draft, args)
        if action == "send":
            return await asyncio.to_thread(self._send, args)
        return f"unknown gmail action: {action}"

    # ── private sync helpers ────────────────────────────────────────────

    def _search(self, args: dict) -> list[dict]:
        query = str(args.get("query", "")).strip()
        max_results = min(max(1, int(args.get("max_results") or 10)), 50)
        resp = (
            self._service.users()
            .threads()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        threads = resp.get("threads", [])
        if not threads:
            return []
        results = []
        for t in threads:
            thread = (
                self._service.users()
                .threads()
                .get(userId="me", id=t["id"], format="metadata",
                     metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
            first_msg = thread.get("messages", [{}])[0]
            headers = {
                h["name"]: h["value"]
                for h in first_msg.get("payload", {}).get("headers", [])
            }
            results.append({
                "thread_id": t["id"],
                "subject": headers.get("Subject", "(no subject)"),
                "from": headers.get("From", ""),
                "date": headers.get("Date", ""),
                "snippet": thread.get("snippet", ""),
            })
        return results

    def _read_thread(self, args: dict) -> list[dict]:
        thread_id = str(args.get("thread_id", "")).strip()
        thread = (
            self._service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        messages = []
        for msg in thread.get("messages", []):
            headers = {
                h["name"]: h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }
            body = _extract_body(msg.get("payload", {}))
            messages.append({
                "message_id": msg["id"],
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "date": headers.get("Date", ""),
                "subject": headers.get("Subject", ""),
                "body": body[:4000],  # cap per message to avoid context bloat
            })
        return messages

    def _create_draft(self, args: dict) -> dict:
        msg_bytes = _build_mime(args)
        body: dict = {"message": {"raw": _b64url(msg_bytes)}}
        thread_id = args.get("reply_to_thread_id")
        if thread_id:
            body["message"]["threadId"] = thread_id
        draft = self._service.users().drafts().create(userId="me", body=body).execute()
        log.info("gmail.draft_created", draft_id=draft.get("id"))
        return {"draft_id": draft.get("id"), "status": "draft created"}

    def _send(self, args: dict) -> dict:
        msg_bytes = _build_mime(args)
        body: dict = {"raw": _b64url(msg_bytes)}
        thread_id = args.get("reply_to_thread_id")
        if thread_id:
            body["threadId"] = thread_id
        sent = self._service.users().messages().send(userId="me", body=body).execute()
        log.info("gmail.sent", message_id=sent.get("id"))
        return {"message_id": sent.get("id"), "status": "sent"}


# ── utilities ───────────────────────────────────────────────────────────────


def _build_mime(args: dict) -> bytes:
    """Build a MIME message from to/subject/body args."""
    msg = MIMEMultipart("alternative")
    msg["To"] = args.get("to", "")
    msg["Subject"] = args.get("subject", "")
    msg.attach(MIMEText(args.get("body", ""), "plain", "utf-8"))
    return msg.as_bytes()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


def _extract_body(payload: dict) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text
    # Fallback: try text/html if no plain text found
    if mime_type == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            # Strip tags crudely — good enough for LLM consumption
            import re
            return re.sub(r"<[^>]+>", " ", raw).strip()
    return ""
