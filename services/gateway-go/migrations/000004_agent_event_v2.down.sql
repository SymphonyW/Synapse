BEGIN;

-- Destructive rollback: removes typed AgentEvent V2 payload data.
ALTER TABLE task_events DROP COLUMN IF EXISTS payload;
ALTER TABLE task_events DROP COLUMN IF EXISTS event_name;
ALTER TABLE task_events DROP COLUMN IF EXISTS schema_version;

COMMIT;
