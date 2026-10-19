-- Resource ledger: money, compute, disk and quota, with enforceable ceilings.
-- `resource_events` is append-only and is the provenance behind any bill;
-- `resource_totals` is the running figure a ceiling is compared against inside
-- the charging transaction.

CREATE TABLE IF NOT EXISTS resource_ceilings (
    account      TEXT NOT NULL,
    resource     TEXT NOT NULL,
    limit_amount DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (account, resource)
);

CREATE TABLE IF NOT EXISTS resource_totals (
    account  TEXT NOT NULL,
    resource TEXT NOT NULL,
    period   TEXT NOT NULL,
    used     DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (account, resource, period)
);

CREATE TABLE IF NOT EXISTS resource_events (
    id       BIGSERIAL PRIMARY KEY,
    account  TEXT NOT NULL,
    resource TEXT NOT NULL,
    period   TEXT NOT NULL,
    amount   DOUBLE PRECISION NOT NULL,
    reason   TEXT NOT NULL DEFAULT '',
    at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS resource_events_account_idx
    ON resource_events (account, resource, period);
