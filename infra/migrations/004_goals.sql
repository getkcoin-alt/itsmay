-- Durable operator goals and Scrappy-inferred next tasks.
-- Planning state is intentionally separate from memory: memory provides context;
-- it never grants authority or silently becomes an executable task.

CREATE TABLE IF NOT EXISTS goals (
    id          TEXT PRIMARY KEY,
    objective   TEXT NOT NULL,
    provenance  TEXT NOT NULL CHECK (
        provenance IN ('operator', 'operator_config', 'scrappy_inferred')
    ),
    source_ref  TEXT,
    status      TEXT NOT NULL CHECK (
        status IN ('active', 'paused', 'completed', 'cancelled')
    ),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_goals_status_created
    ON goals (status, created_at);

CREATE TABLE IF NOT EXISTS goal_tasks (
    id               TEXT PRIMARY KEY,
    goal_id          TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    title            TEXT NOT NULL,
    rationale        TEXT NOT NULL DEFAULT '',
    success_criteria JSONB NOT NULL DEFAULT '[]'::jsonb,
    dependencies     JSONB NOT NULL DEFAULT '[]'::jsonb,
    provenance       TEXT NOT NULL CHECK (
        provenance IN ('operator', 'scrappy_planner')
    ),
    source_ref       TEXT,
    state            TEXT NOT NULL CHECK (
        state IN ('ready', 'active', 'blocked', 'completed', 'failed', 'cancelled')
    ),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_goal_tasks_goal_state_created
    ON goal_tasks (goal_id, state, created_at);
