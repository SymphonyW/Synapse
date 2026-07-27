BEGIN;

CREATE TABLE tool_policies (
  id TEXT PRIMARY KEY,
  role_allow JSONB NOT NULL DEFAULT '{}'::jsonb,
  approval_required JSONB NOT NULL DEFAULT '[]'::jsonb,
  disabled_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
  version BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL,
  updated_by TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT ''
);

COMMIT;
