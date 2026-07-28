BEGIN;

CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  prompt TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  replay_of_task_id TEXT NULL REFERENCES tasks(id) ON DELETE SET NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE task_events (
  id BIGSERIAL PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  token TEXT NOT NULL DEFAULT '',
  trace_id TEXT NOT NULL DEFAULT '',
  emitted_at_unix_ms BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE dead_letter_tasks (
  task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
  reason TEXT NOT NULL,
  attempts INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE auth_users (
  username TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE auth_sessions (
  token TEXT PRIMARY KEY,
  username TEXT NOT NULL REFERENCES auth_users(username) ON DELETE CASCADE,
  role TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_task_events_task_id_id ON task_events (task_id, id);
CREATE INDEX idx_auth_sessions_username ON auth_sessions (username);
CREATE INDEX idx_auth_sessions_expires_at ON auth_sessions (expires_at);

COMMIT;
