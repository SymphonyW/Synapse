BEGIN;

CREATE INDEX idx_tasks_user_conversation_created
  ON tasks (user_id, (metadata->>'conversation_id'), created_at DESC);

CREATE INDEX idx_tasks_replay_of_created
  ON tasks (replay_of_task_id, created_at DESC);

COMMIT;
