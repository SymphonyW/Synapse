BEGIN;

DROP INDEX IF EXISTS idx_tasks_replay_of_created;
DROP INDEX IF EXISTS idx_tasks_user_conversation_created;

COMMIT;
