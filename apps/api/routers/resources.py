"""Authenticated control surface for the resource ledger.

Reading usage is reading the bill, and raising a ceiling is granting spend — so
this sits behind the same bearer middleware as every other `/v1` route.

Only the operator sets ceilings. Nothing in Scrappy's own tool path can reach
these endpoints to widen its own allowance; that asymmetry is the point of
having a ceiling at all.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.resources import Resource

router = APIRouter(prefix="/v1/resources", tags=["resources"])


class CeilingBody(BaseModel):
    limit: float | None = Field(
        default=None,
        description="New ceiling; null removes it and makes the resource uncapped.",
    )


def _ledger(request: Request):
    ledger = getattr(request.app.state, "ledger", None)
    if ledger is None:
        raise HTTPException(status_code=503, detail="resource ledger unavailable")
    return ledger


@router.get("")
async def usage(request: Request, account: str | None = None) -> dict:
    """Everything metered for this account, with ceilings and headroom."""
    ledger = _ledger(request)
    rows = await ledger.report(account=account)
    return {
        "account": account or ledger.account,
        "usage": [u.to_dict() for u in rows],
    }


@router.get("/{resource}")
async def usage_for(request: Request, resource: Resource, account: str | None = None) -> dict:
    ledger = _ledger(request)
    return (await ledger.usage(resource, account=account)).to_dict()


@router.put("/{resource}/ceiling")
async def set_ceiling(
    resource: Resource,
    body: CeilingBody,
    request: Request,
    account: str | None = None,
) -> dict:
    """Set or remove one ceiling. `limit: null` means uncapped."""
    ledger = _ledger(request)
    if body.limit is None:
        await ledger.clear_ceiling(resource, account=account)
    else:
        try:
            await ledger.set_ceiling(resource, body.limit, account=account)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return (await ledger.usage(resource, account=account)).to_dict()
