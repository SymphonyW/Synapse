BEGIN;

-- Destructive rollback: drops persisted tool policy configuration.
DROP TABLE IF EXISTS tool_policies;

COMMIT;
