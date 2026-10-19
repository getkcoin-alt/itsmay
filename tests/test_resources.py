"""The resource ledger: metering, and ceilings that actually refuse.

The tests worth having here are the ones about *refusal*, not counting. A counter
is easy; a limit that cannot be talked past under concurrency, that leaves no
residue when it refuses, and that survives a restart, is the thing
`resource-ceilings` is asking for.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from core.resources import (
    TOTAL,
    CeilingExceeded,
    Kind,
    Resource,
    ResourceLedger,
    SqliteResourceStore,
)
from core.resources.models import period_for


@pytest.fixture
def ledger(tmp_path):
    store = SqliteResourceStore(str(tmp_path / "res.db"))
    try:
        yield ResourceLedger(store, account="acct")
    finally:
        store.close()


# ── periods ───────────────────────────────────────────────────────────


def test_a_stock_shares_one_bucket_and_a_flow_is_monthly():
    # Disk held does not reset in January; searches run do.
    assert Resource.MEMORIES.kind is Kind.STOCK
    assert Resource.SEARCHES.kind is Kind.FLOW
    assert period_for(Resource.MEMORIES) == TOTAL
    jan = datetime(2026, 1, 9, tzinfo=UTC)
    feb = datetime(2026, 2, 1, tzinfo=UTC)
    assert period_for(Resource.SEARCHES, jan) == "2026-01"
    assert period_for(Resource.SEARCHES, feb) == "2026-02"
    assert period_for(Resource.MEMORIES, feb) == TOTAL


def test_period_is_computed_in_utc_not_local_time():
    # 23:30 on the 31st in UTC+5:30 is still the 31st in UTC; a ledger that used
    # local time would bill it to the next month.
    late = datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
    assert period_for(Resource.SEARCHES, late) == "2026-01"


async def test_a_new_month_starts_a_flow_from_zero(ledger):
    jan, feb = datetime(2026, 1, 15, tzinfo=UTC), datetime(2026, 2, 15, tzinfo=UTC)
    await ledger.set_ceiling(Resource.SEARCHES, 2)
    await ledger.charge(Resource.SEARCHES, 2, when=jan)
    with pytest.raises(CeilingExceeded):
        await ledger.charge(Resource.SEARCHES, 1, when=jan)
    # February is a fresh bucket against the same ceiling.
    assert await ledger.charge(Resource.SEARCHES, 2, when=feb) == 2


# ── enforcement ───────────────────────────────────────────────────────


async def test_uncapped_resources_are_metered_but_never_refused(ledger):
    for _ in range(50):
        await ledger.charge(Resource.TOKENS, 1000)
    usage = await ledger.usage(Resource.TOKENS)
    assert usage.used == 50_000
    assert usage.ceiling is None
    # Uncapped is not the same as "nothing left", and must not read as exhausted.
    assert usage.remaining is None
    assert usage.exhausted is False
    assert await ledger.remaining(Resource.TOKENS) is None


async def test_a_charge_to_exactly_the_ceiling_is_allowed(ledger):
    await ledger.set_ceiling(Resource.USD, 10.0)
    assert await ledger.charge(Resource.USD, 10.0) == 10.0
    usage = await ledger.usage(Resource.USD)
    assert usage.remaining == 0 and usage.exhausted is True


async def test_the_charge_that_would_breach_is_refused(ledger):
    await ledger.set_ceiling(Resource.USD, 10.0)
    await ledger.charge(Resource.USD, 9.5)
    with pytest.raises(CeilingExceeded) as exc:
        await ledger.charge(Resource.USD, 1.0)
    e = exc.value
    assert (e.used, e.requested, e.ceiling) == (9.5, 1.0, 10.0)
    assert e.resource is Resource.USD and e.account == "acct"


async def test_a_refused_charge_leaves_no_residue(ledger):
    """The whole point: a breach must not move the total or log an event."""
    await ledger.set_ceiling(Resource.MEMORIES, 1)
    await ledger.charge(Resource.MEMORIES, 1)
    before = await ledger.usage(Resource.MEMORIES)
    with pytest.raises(CeilingExceeded):
        await ledger.charge(Resource.MEMORIES, 5)
    assert (await ledger.usage(Resource.MEMORIES)).used == before.used

    events = ledger.store._db  # the append-only trail must agree
    with events.cursor() as cur:
        rows = cur.execute("SELECT amount FROM resource_events").fetchall()
    assert [r["amount"] for r in rows] == [1.0]


async def test_a_zero_ceiling_refuses_everything(ledger):
    # A disabled account is a real state, and must not read as "uncapped".
    await ledger.set_ceiling(Resource.CALLS, 0)
    assert await ledger.ceiling(Resource.CALLS) == 0
    with pytest.raises(CeilingExceeded):
        await ledger.charge(Resource.CALLS, 1)


async def test_concurrent_charges_cannot_talk_past_the_ceiling(ledger):
    """Ten racing requests against a ceiling of 5 — exactly 5 may win.

    A read-then-write ledger lets several of these each observe "under the
    limit" and all commit. That is the bug this test exists for.
    """
    await ledger.set_ceiling(Resource.CALLS, 5)
    results = await asyncio.gather(
        *(ledger.charge(Resource.CALLS, 1) for _ in range(10)),
        return_exceptions=True,
    )
    allowed = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, CeilingExceeded)]
    assert len(allowed) == 5, f"ceiling leaked: {len(allowed)} charges allowed"
    assert len(refused) == 5
    assert (await ledger.usage(Resource.CALLS)).used == 5


async def test_raising_a_ceiling_unblocks_without_losing_history(ledger):
    await ledger.set_ceiling(Resource.USD, 1.0)
    await ledger.charge(Resource.USD, 1.0)
    with pytest.raises(CeilingExceeded):
        await ledger.charge(Resource.USD, 1.0)
    await ledger.set_ceiling(Resource.USD, 5.0)
    assert await ledger.charge(Resource.USD, 1.0) == 2.0  # kept the first dollar


async def test_clearing_a_ceiling_makes_the_resource_uncapped(ledger):
    await ledger.set_ceiling(Resource.CALLS, 1)
    await ledger.charge(Resource.CALLS, 1)
    with pytest.raises(CeilingExceeded):
        await ledger.charge(Resource.CALLS, 1)
    await ledger.clear_ceiling(Resource.CALLS)
    await ledger.charge(Resource.CALLS, 100)
    assert await ledger.ceiling(Resource.CALLS) is None


async def test_a_negative_ceiling_is_rejected(ledger):
    with pytest.raises(ValueError):
        await ledger.set_ceiling(Resource.USD, -1)


async def test_charge_refuses_a_negative_amount(ledger):
    # Otherwise "charging" -5 is a silent way to mint headroom.
    with pytest.raises(ValueError):
        await ledger.charge(Resource.USD, -5)


# ── release ───────────────────────────────────────────────────────────


async def test_releasing_a_stock_frees_headroom(ledger):
    await ledger.set_ceiling(Resource.MEMORIES, 2)
    await ledger.charge(Resource.MEMORIES, 2)
    await ledger.release(Resource.MEMORIES, 1, reason="memory deleted")
    assert await ledger.charge(Resource.MEMORIES, 1) == 2


async def test_release_is_clamped_at_zero(ledger):
    await ledger.charge(Resource.MEMORIES, 1)
    await ledger.release(Resource.MEMORIES, 10)
    assert (await ledger.usage(Resource.MEMORIES)).used == 0
    # The clamp must not become negative headroom that outlives the mistake.
    await ledger.set_ceiling(Resource.MEMORIES, 1)
    await ledger.charge(Resource.MEMORIES, 1)
    with pytest.raises(CeilingExceeded):
        await ledger.charge(Resource.MEMORIES, 1)


# ── reserve ───────────────────────────────────────────────────────────


async def test_reserve_refuses_before_the_body_runs(ledger):
    await ledger.set_ceiling(Resource.CALLS, 1)
    await ledger.charge(Resource.CALLS, 1)
    ran = False
    with pytest.raises(CeilingExceeded):
        async with ledger.reserve(Resource.CALLS):
            ran = True
    assert ran is False, "the ceiling must be enforced before the work, not after"


async def test_reserve_refunds_when_the_body_raises(ledger):
    await ledger.set_ceiling(Resource.MEMORIES, 1)
    with pytest.raises(RuntimeError):
        async with ledger.reserve(Resource.MEMORIES, keep=False):
            raise RuntimeError("the write failed")
    assert (await ledger.usage(Resource.MEMORIES)).used == 0
    await ledger.charge(Resource.MEMORIES, 1)  # headroom genuinely came back


async def test_reserve_keeps_the_charge_for_consumed_resources(ledger):
    async with ledger.reserve(Resource.TOKENS, 100):
        pass
    assert (await ledger.usage(Resource.TOKENS)).used == 100


async def test_reserve_can_refund_a_stock_that_was_not_created(ledger):
    async with ledger.reserve(Resource.MEMORIES, 1, keep=False):
        pass
    assert (await ledger.usage(Resource.MEMORIES)).used == 0


# ── durability and isolation ──────────────────────────────────────────


async def test_the_ledger_survives_a_restart(tmp_path):
    path = str(tmp_path / "res.db")
    store = SqliteResourceStore(path)
    first = ResourceLedger(store, account="acct")
    await first.set_ceiling(Resource.MEMORIES, 3)
    await first.charge(Resource.MEMORIES, 3)
    store.close()

    reopened = SqliteResourceStore(path)
    try:
        second = ResourceLedger(reopened, account="acct")
        assert await second.ceiling(Resource.MEMORIES) == 3
        assert (await second.usage(Resource.MEMORIES)).used == 3
        # The ceiling is still enforced after the process came back.
        with pytest.raises(CeilingExceeded):
            await second.charge(Resource.MEMORIES, 1)
    finally:
        reopened.close()


async def test_accounts_do_not_share_totals_or_ceilings(ledger):
    await ledger.set_ceiling(Resource.CALLS, 1, account="a")
    await ledger.charge(Resource.CALLS, 1, account="a")
    with pytest.raises(CeilingExceeded):
        await ledger.charge(Resource.CALLS, 1, account="a")
    # `b` is untouched by `a` hitting its limit — the premise of per-key billing.
    assert await ledger.charge(Resource.CALLS, 1, account="b") == 1
    assert await ledger.ceiling(Resource.CALLS, account="b") is None


async def test_report_is_the_bill(ledger):
    await ledger.set_ceiling(Resource.SEARCHES, 100)
    await ledger.charge(Resource.SEARCHES, 7)
    await ledger.charge(Resource.MEMORIES, 2)
    await ledger.charge(Resource.USD, 0.25)

    report = {u.resource: u for u in await ledger.report()}
    assert report[Resource.SEARCHES].used == 7
    assert report[Resource.SEARCHES].ceiling == 100
    assert report[Resource.SEARCHES].remaining == 93
    assert report[Resource.MEMORIES].period == TOTAL
    assert report[Resource.USD].used == 0.25
    assert all(u.account == "acct" for u in report.values())
    assert [u.to_dict()["resource"] for u in report.values()]  # serialises for an API


async def test_an_unknown_resource_row_is_skipped_rather_than_crashing_the_bill(ledger):
    """Forward compatibility: a newer build may meter something this one has no
    name for. The bill should be short one line, not absent."""
    await ledger.charge(Resource.USD, 1)
    with ledger.store._db.cursor(write=True) as cur:
        cur.execute(
            "INSERT INTO resource_totals (account, resource, period, used) "
            "VALUES ('acct', 'quantum_flux', 'total', 5)"
        )
    report = await ledger.report()
    assert [u.resource for u in report] == [Resource.USD]
