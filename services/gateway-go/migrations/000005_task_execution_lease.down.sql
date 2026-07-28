BEGIN;

-- Destructive rollback: removes execution lease ownership and attempt counters.
DROP INDEX IF EXISTS idx_tasks_execution_lease;
ALTER TABLE tasks DROP COLUMN IF EXISTS execution_attempt;
ALTER TABLE tasks DROP COLUMN IF EXISTS execution_lease_until;
ALTER TABLE tasks DROP COLUMN IF EXISTS execution_owner;

COMMIT;
