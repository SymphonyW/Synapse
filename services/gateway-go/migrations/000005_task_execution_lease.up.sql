BEGIN;

ALTER TABLE tasks ADD COLUMN execution_owner TEXT NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN execution_lease_until TIMESTAMPTZ NULL;
ALTER TABLE tasks ADD COLUMN execution_attempt INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_tasks_execution_lease
  ON tasks (status, execution_lease_until);

COMMIT;
