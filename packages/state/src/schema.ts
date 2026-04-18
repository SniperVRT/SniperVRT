// Canonical SQL schema. Kept as a string export so scripts/migrate.ts can run
// it, and so tests can assert against the exact DDL. Not using a full ORM -
// the durable rows are few and the query surface is narrow.
export const POSTGRES_SCHEMA = `
CREATE TABLE IF NOT EXISTS tasks (
  id                   TEXT PRIMARY KEY,
  goal                 JSONB NOT NULL,
  status               TEXT NOT NULL,
  priority             INT NOT NULL DEFAULT 50,
  risk_level           TEXT NOT NULL DEFAULT 'low',
  plan                 JSONB,
  current_step_id      TEXT,
  approvals_required   INT NOT NULL DEFAULT 0,
  approvals_received   INT NOT NULL DEFAULT 0,
  result               JSONB,
  mode                 TEXT NOT NULL DEFAULT 'normal',
  tokens_used          INT NOT NULL DEFAULT 0,
  usd_spent            NUMERIC NOT NULL DEFAULT 0,
  attempts             INT NOT NULL DEFAULT 0,
  tenant_id            TEXT,
  user_id              TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tasks_status_priority_idx ON tasks (status, priority DESC);
CREATE INDEX IF NOT EXISTS tasks_tenant_idx ON tasks (tenant_id);

CREATE TABLE IF NOT EXISTS steps (
  id                   TEXT PRIMARY KEY,
  task_id              TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  plan_step_id         TEXT NOT NULL,
  description          TEXT NOT NULL,
  assigned_agent       TEXT NOT NULL,
  connector            TEXT,
  action_type          TEXT NOT NULL,
  risk_level           TEXT NOT NULL,
  input_payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
  output_payload       JSONB,
  status               TEXT NOT NULL,
  retries              INT NOT NULL DEFAULT 0,
  max_retries          INT NOT NULL DEFAULT 2,
  last_error           JSONB,
  confidence           NUMERIC NOT NULL DEFAULT 0.7,
  idempotency_key      TEXT,
  started_at           TIMESTAMPTZ,
  finished_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS steps_task_idx ON steps (task_id);

CREATE TABLE IF NOT EXISTS approvals (
  id                   TEXT PRIMARY KEY,
  task_id              TEXT NOT NULL,
  step_id              TEXT NOT NULL,
  payload              JSONB NOT NULL,
  status               TEXT NOT NULL,
  requested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  responded_at         TIMESTAMPTZ,
  responded_by         TEXT,
  expires_at           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS approvals_task_idx ON approvals (task_id, status);

CREATE TABLE IF NOT EXISTS traces (
  id                   TEXT PRIMARY KEY,
  task_id              TEXT NOT NULL,
  step_id              TEXT,
  kind                 TEXT NOT NULL,
  agent                TEXT,
  connector            TEXT,
  summary              TEXT NOT NULL,
  payload_hash         TEXT,
  status               TEXT NOT NULL DEFAULT 'ok',
  tokens_used          INT NOT NULL DEFAULT 0,
  latency_ms           INT NOT NULL DEFAULT 0,
  timestamp            TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata             JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS traces_task_idx ON traces (task_id, timestamp);

CREATE TABLE IF NOT EXISTS memory_entries (
  id                   TEXT PRIMARY KEY,
  kind                 TEXT NOT NULL,
  scope                TEXT NOT NULL,
  tenant_id            TEXT,
  user_id              TEXT,
  task_id              TEXT,
  title                TEXT NOT NULL,
  body                 TEXT NOT NULL,
  tags                 TEXT[] NOT NULL DEFAULT '{}',
  data                 JSONB NOT NULL DEFAULT '{}'::jsonb,
  confidence           NUMERIC NOT NULL DEFAULT 0.7,
  usefulness           NUMERIC NOT NULL DEFAULT 0.5,
  use_count            INT NOT NULL DEFAULT 0,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS memory_scope_idx ON memory_entries (scope, kind);
CREATE INDEX IF NOT EXISTS memory_tags_idx ON memory_entries USING GIN (tags);

CREATE TABLE IF NOT EXISTS connectors (
  id                   TEXT PRIMARY KEY,
  service_name         TEXT NOT NULL,
  version              TEXT NOT NULL DEFAULT '1.0.0',
  transport            TEXT NOT NULL,
  auth_kind            TEXT NOT NULL,
  descriptor           JSONB NOT NULL,
  health               TEXT NOT NULL DEFAULT 'unknown',
  enabled              BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
`;
