"""Read-only SYNCBOND bridge from Vault Zeta to Scrappy OS.

Phase 1 deliberately exposes only READ-risk machine objectives. Vault can ask
the machine control plane to observe/inspect reality, but this connector cannot
submit WRITE, PRIVILEGED or DESTRUCTIVE objectives.

The connector also exposes one approval-gated continuity write:
``record_terminal_experience`` fetches a terminal task result, verifies that
Scrappy OS echoed the originating correlation id, deterministically distills
structured evidence, and stores one idempotent Vault Experience. It never treats
model prose as proof that an action succeeded.

Configuration:
    SCRAPPY_OS_BASE_URL=http://127.0.0.1:8787
    SCRAPPY_OS_API_TOKEN=scrp_...

The token should belong to a dedicated Vault service principal with only:
    task:create, task:read, system:read

Scrappy OS remains the authority for actor identity, policy, execution,
approvals and audit. Vault stores only the deliberately distilled continuity
record, not a copy of the raw operational log.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx

from core.connectors.base import Connector, ConnectorManifest, InvocationContext, ToolSpec
from core.continuity.experiences import SyncbondExperienceStore, distill_scrappy_os_status
from core.contracts.syncbond import ActorKind, EventType, Objective, envelope
from core.logging import get_logger

log = get_logger(__name__)

_TIMEOUT_SECONDS = 15.0
_MAX_OBJECTIVE_CHARS = 8000

_TOOLS = [
    ToolSpec(
        name="health",
        description=(
            "Read Scrappy OS liveness/health. This does not execute a machine task."
        ),
        parameters={"type": "object", "properties": {}},
        executor="server",
    ),
    ToolSpec(
        name="submit_read_objective",
        description=(
            "Ask Scrappy OS to inspect or observe machine state. The bridge hard-caps "
            "the objective at READ risk; it cannot create, modify, delete, restart, "
            "install, kill, or otherwise mutate machine state."
        ),
        parameters={
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": _MAX_OBJECTIVE_CHARS,
                    "description": "A concrete read-only machine observation objective.",
                },
                "success_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional evidence conditions that define completion.",
                },
            },
            "required": ["objective"],
            "additionalProperties": False,
        },
        executor="server",
        side_effects=["creates a read-only task in the Scrappy OS control plane"],
    ),
    ToolSpec(
        name="task_status",
        description=(
            "Read the current or final status of a previously submitted Scrappy OS task."
        ),
        parameters={
            "type": "object",
            "properties": {
                "objective_id": {
                    "type": "string",
                    "description": "Scrappy OS objective id returned by submit_read_objective.",
                }
            },
            "required": ["objective_id"],
            "additionalProperties": False,
        },
        executor="server",
    ),
    ToolSpec(
        name="record_terminal_experience",
        description=(
            "Persist one verified terminal Scrappy OS outcome as a durable Vault Experience. "
            "The remote task must echo the exact originating SYNCBOND correlation id. "
            "This changes durable memory and therefore requires explicit approval."
        ),
        parameters={
            "type": "object",
            "properties": {
                "objective_id": {
                    "type": "string",
                    "description": "Scrappy OS objective id returned by submit_read_objective.",
                },
                "correlation_id": {
                    "type": "string",
                    "description": "SYNCBOND correlation id from the originating objective envelope.",
                },
            },
            "required": ["objective_id", "correlation_id"],
            "additionalProperties": False,
        },
        executor="server",
        requires_approval=True,
        side_effects=["writes an idempotent durable SYNCBOND Experience in Vault Zeta"],
    ),
]


class ScrappyOSConnector(Connector):
    manifest = ConnectorManifest(
        name="scrappy_os",
        version="0.2.0",
        description="Read-only Scrappy OS bridge with approval-gated verified Experience ingestion.",
        tools=_TOOLS,
    )

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
        experience_store: SyncbondExperienceStore | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("SCRAPPY_OS_BASE_URL", "")).strip().rstrip("/")
        self.token = (token or os.getenv("SCRAPPY_OS_API_TOKEN", "")).strip()
        self._client = client
        self._experience_store = experience_store or SyncbondExperienceStore()
        if self.base_url:
            self._validate_base_url(self.base_url)

    @staticmethod
    def _validate_base_url(value: str) -> None:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("SCRAPPY_OS_BASE_URL must be an absolute http(s) URL")
        if parsed.username or parsed.password:
            raise ValueError("credentials must not be embedded in SCRAPPY_OS_BASE_URL")
        if parsed.query or parsed.fragment:
            raise ValueError("SCRAPPY_OS_BASE_URL must not contain query/fragment data")

    def _require_base_url(self) -> str:
        if not self.base_url:
            raise RuntimeError("SCRAPPY_OS_BASE_URL is not configured")
        return self.base_url

    def _require_token(self) -> str:
        if not self.token:
            raise RuntimeError("SCRAPPY_OS_API_TOKEN is not configured")
        return self.token

    def _headers(self, *, require_auth: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        token = self._require_token() if require_auth else self.token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self._require_base_url()}{path}"
        if self._client is not None:
            return await self._client.request(method, url, **kwargs)
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=False) as client:
            return await client.request(method, url, **kwargs)

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        try:
            value = response.json()
        except ValueError as exc:
            raise RuntimeError("Scrappy OS returned non-JSON data") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Scrappy OS returned an unexpected response shape")
        return value

    @staticmethod
    def _uuid(value: Any, name: str) -> UUID:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{name} is required")
        try:
            return UUID(text)
        except ValueError as exc:
            raise ValueError(f"{name} must be a UUID") from exc

    async def invoke(self, action: str, args: dict, ctx: InvocationContext) -> Any:
        if action == "health":
            response = await self._request(
                "GET",
                "/health",
                headers=self._headers(require_auth=False),
            )
            result = self._json(response)
            return {
                "healthy": result.get("healthy"),
                "status": result.get("status"),
                "version": result.get("version"),
                "uptime_seconds": result.get("uptime_seconds"),
            }

        if action == "submit_read_objective":
            text = str(args.get("objective", "")).strip()
            if not text:
                raise ValueError("objective is required")
            if len(text) > _MAX_OBJECTIVE_CHARS:
                raise ValueError(f"objective exceeds {_MAX_OBJECTIVE_CHARS} characters")

            criteria = [
                str(item).strip()
                for item in (args.get("success_criteria") or [])
                if str(item).strip()
            ][:20]
            correlation_id = uuid4()
            contract_objective = Objective(
                statement=text,
                constraints=[
                    "read-only execution",
                    "Scrappy OS remains the execution/policy authority",
                ],
                success_criteria=criteria,
                max_risk="read",
            )
            requested = envelope(
                actor_id="service:vault-zeta",
                actor_kind=ActorKind.SERVICE,
                event_type=EventType.OBJECTIVE_REQUESTED,
                source="vault-zeta",
                payload=contract_objective,
                correlation_id=correlation_id,
            )

            response = await self._request(
                "POST",
                "/tasks",
                headers={
                    **self._headers(require_auth=True),
                    "Content-Type": "application/json",
                    "X-Syncbond-Correlation-ID": str(correlation_id),
                    "X-Syncbond-Version": requested.schema_version,
                },
                json={
                    "objective": text,
                    "max_risk": "read",
                    "dry_run": False,
                },
            )
            remote = self._json(response)
            remote_id = str(remote.get("objective_id") or "").strip()
            if not remote_id:
                raise RuntimeError("Scrappy OS accepted a task without returning objective_id")
            self._uuid(remote_id, "remote objective_id")

            echoed = remote.get("correlation_id")
            if echoed is not None and self._uuid(echoed, "remote correlation_id") != correlation_id:
                raise RuntimeError("Scrappy OS returned a mismatched correlation_id")

            log.info(
                "scrappy_os.objective_submitted",
                correlation_id=str(correlation_id),
                remote_objective_id=remote_id,
                user_id=ctx.user_id,
                session_id=ctx.session_id,
            )
            return {
                "syncbond": requested.model_dump(mode="json"),
                "remote_objective_id": remote_id,
                "remote_status": remote.get("status"),
                "events_url": remote.get("events_url"),
            }

        if action == "task_status":
            remote_id = self._uuid(args.get("objective_id"), "objective_id")
            response = await self._request(
                "GET",
                f"/tasks/{remote_id}",
                headers=self._headers(require_auth=True),
            )
            result = self._json(response)
            return {"remote": result}

        if action == "record_terminal_experience":
            remote_id = self._uuid(args.get("objective_id"), "objective_id")
            correlation_id = self._uuid(args.get("correlation_id"), "correlation_id")
            response = await self._request(
                "GET",
                f"/tasks/{remote_id}",
                headers=self._headers(require_auth=True),
            )
            remote = self._json(response)
            distilled = distill_scrappy_os_status(
                remote,
                expected_correlation_id=correlation_id,
            )
            user_uuid = ctx.user_uuid if isinstance(ctx.user_uuid, UUID) else None
            stored, created = await self._experience_store.put(distilled, user_id=user_uuid)
            log.info(
                "scrappy_os.experience_recorded",
                correlation_id=str(correlation_id),
                remote_objective_id=str(remote_id),
                created=created,
                experience_id=str(stored.get("id")),
            )
            return {
                "created": created,
                "experience_id": str(stored.get("id")),
                "outcome": distilled.experience.outcome,
                "summary": distilled.experience.summary,
                "syncbond": distilled.envelope.model_dump(mode="json"),
            }

        raise ValueError(f"unknown scrappy_os action: {action}")

    async def health(self) -> dict:
        if not self.base_url:
            return {"name": self.manifest.name, "ok": False, "detail": "base URL not configured"}
        try:
            result = await self.invoke("health", {}, InvocationContext())
            return {"name": self.manifest.name, "ok": bool(result.get("healthy")), "detail": result}
        except Exception as exc:  # health must report failure, not break registry status
            return {"name": self.manifest.name, "ok": False, "detail": str(exc)}
