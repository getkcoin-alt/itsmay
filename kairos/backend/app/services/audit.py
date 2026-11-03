from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AuditLog, User


def audit(
    db: Session,
    action: str,
    actor: User | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> None:
    """Append an audit record in the caller's transaction."""
    db.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
            ip=ip,
        )
    )
