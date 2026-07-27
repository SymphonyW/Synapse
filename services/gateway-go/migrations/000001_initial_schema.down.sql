BEGIN;

-- Destructive rollback: drops all Gateway task, event, auth, and dead-letter data.
DROP TABLE IF EXISTS auth_sessions;
DROP TABLE IF EXISTS auth_users;
DROP TABLE IF EXISTS dead_letter_tasks;
DROP TABLE IF EXISTS task_events;
DROP TABLE IF EXISTS tasks;

COMMIT;
