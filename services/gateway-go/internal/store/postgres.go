package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/lib/pq"
	"github.com/synapse/synapse/services/gateway-go/internal/domain"
)

// dbOperationTimeout 为单次数据库操作设置上限，避免在数据库压力下无限阻塞。
const dbOperationTimeout = 3 * time.Second

// PostgresStore 是基于 PostgreSQL 的持久化 TaskStore 实现。
type PostgresStore struct {
	db *sql.DB
}

// NewPostgres 建立连接、校验可达性并确保所需表结构存在。
func NewPostgres(ctx context.Context, databaseURL string) (*PostgresStore, error) {
	db, err := sql.Open("postgres", databaseURL)
	if err != nil {
		return nil, err
	}

	store := &PostgresStore{db: db}
	if err := store.db.PingContext(ctx); err != nil {
		_ = db.Close()
		return nil, err
	}

	if err := store.ensureSchema(ctx); err != nil {
		_ = db.Close()
		return nil, err
	}

	return store, nil
}

// Close 释放底层 sql.DB 资源。
func (s *PostgresStore) Close() error {
	if s.db == nil {
		return nil
	}
	return s.db.Close()
}

// Create 插入新任务；主键冲突会转换为 ErrTaskAlreadyExists。
func (s *PostgresStore) Create(task domain.Task) error {
	if task.Metadata == nil {
		task.Metadata = map[string]string{}
	}

	metadataJSON, err := json.Marshal(task.Metadata)
	if err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	// metadata 使用 JSONB 存储，便于后续字段扩展而无需频繁改表。
	_, err = s.db.ExecContext(
		ctx,
		`INSERT INTO tasks (id, user_id, prompt, status, error, metadata, created_at, updated_at)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
		task.ID,
		task.UserID,
		task.Prompt,
		string(task.Status),
		task.Error,
		metadataJSON,
		task.CreatedAt,
		task.UpdatedAt,
	)
	if err != nil {
		var pqErr *pq.Error
		if errors.As(err, &pqErr) && pqErr.Code == "23505" {
			// 主键 task.id 唯一约束冲突。
			return ErrTaskAlreadyExists
		}
		return err
	}

	return nil
}

// Get 按 ID 查询任务。
func (s *PostgresStore) Get(taskID string) (domain.Task, bool) {
	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	row := s.db.QueryRowContext(
		ctx,
		`SELECT id, user_id, prompt, status, error, metadata, created_at, updated_at
		 FROM tasks WHERE id = $1`,
		taskID,
	)

	task, err := scanTask(row)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.Task{}, false
	}
	if err != nil {
		return domain.Task{}, false
	}

	return task, true
}

// ListTasks 按 updated_at 倒序返回任务，支持状态过滤与数量限制。
func (s *PostgresStore) ListTasks(limit int, status string) ([]domain.Task, error) {
	if limit <= 0 {
		limit = 50
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	// 仅在存在 status 过滤时动态拼接 SQL，仍保持占位符安全与顺序一致。
	query := `SELECT id, user_id, prompt, status, error, metadata, created_at, updated_at
		 FROM tasks`
	args := make([]any, 0, 2)
	if status != "" {
		query += ` WHERE status = $1`
		args = append(args, status)
		query += ` ORDER BY updated_at DESC LIMIT $2`
		args = append(args, limit)
	} else {
		query += ` ORDER BY updated_at DESC LIMIT $1`
		args = append(args, limit)
	}

	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	tasks := make([]domain.Task, 0)
	for rows.Next() {
		task, err := scanTask(rows)
		if err != nil {
			return nil, err
		}
		tasks = append(tasks, task)
	}

	if err := rows.Err(); err != nil {
		return nil, err
	}

	return tasks, nil
}

// ListTasksByConversation 按用户和会话返回历史任务，按创建时间升序返回。
func (s *PostgresStore) ListTasksByConversation(userID string, conversationID string, limit int) ([]domain.Task, error) {
	trimmedUserID := strings.TrimSpace(userID)
	trimmedConversationID := strings.TrimSpace(conversationID)
	if trimmedUserID == "" || trimmedConversationID == "" {
		return []domain.Task{}, nil
	}

	if limit <= 0 {
		limit = 20
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	rows, err := s.db.QueryContext(
		ctx,
		`SELECT id, user_id, prompt, status, error, metadata, created_at, updated_at
		 FROM tasks
		 WHERE user_id = $1 AND metadata->>'conversation_id' = $2
		 ORDER BY created_at DESC
		 LIMIT $3`,
		trimmedUserID,
		trimmedConversationID,
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	tasks := make([]domain.Task, 0)
	for rows.Next() {
		task, err := scanTask(rows)
		if err != nil {
			return nil, err
		}
		tasks = append(tasks, task)
	}

	if err := rows.Err(); err != nil {
		return nil, err
	}

	for left, right := 0, len(tasks)-1; left < right; left, right = left+1, right-1 {
		tasks[left], tasks[right] = tasks[right], tasks[left]
	}

	return tasks, nil
}

// UpdateStatus 原子更新任务状态并返回最新任务快照。
func (s *PostgresStore) UpdateStatus(taskID string, status domain.TaskStatus, errorMessage string) (domain.Task, bool) {
	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	row := s.db.QueryRowContext(
		ctx,
		`UPDATE tasks
		 SET status = $2, error = $3, updated_at = NOW()
		 WHERE id = $1
		 RETURNING id, user_id, prompt, status, error, metadata, created_at, updated_at`,
		taskID,
		string(status),
		errorMessage,
	)

	task, err := scanTask(row)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.Task{}, false
	}
	if err != nil {
		return domain.Task{}, false
	}

	return task, true
}

// AppendEvent 追加任务事件；若外键任务不存在，映射为 ErrTaskNotFound。
func (s *PostgresStore) AppendEvent(taskID string, event domain.TaskEvent) (domain.TaskEvent, error) {
	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	if event.Type == "" {
		event.Type = "info"
	}
	if event.EmittedAtUnixMS == 0 {
		event.EmittedAtUnixMS = time.Now().UTC().UnixMilli()
	}

	row := s.db.QueryRowContext(
		ctx,
		`INSERT INTO task_events (task_id, event_type, message, token, trace_id, emitted_at_unix_ms)
		 VALUES ($1, $2, $3, $4, $5, $6)
		 RETURNING id, created_at`,
		taskID,
		event.Type,
		event.Message,
		event.Token,
		event.TraceID,
		event.EmittedAtUnixMS,
	)

	if err := row.Scan(&event.ID, &event.CreatedAt); err != nil {
		var pqErr *pq.Error
		if errors.As(err, &pqErr) && pqErr.Code == "23503" {
			// 外键约束冲突：引用任务不存在。
			return domain.TaskEvent{}, ErrTaskNotFound
		}
		return domain.TaskEvent{}, err
	}

	event.TaskID = taskID
	return event, nil
}

// ListEvents 返回 afterEventID 之后的新事件，按事件 ID 升序。
func (s *PostgresStore) ListEvents(taskID string, afterEventID int64, limit int) ([]domain.TaskEvent, error) {
	if limit <= 0 {
		limit = 200
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	rows, err := s.db.QueryContext(
		ctx,
		`SELECT id, task_id, event_type, message, token, trace_id, emitted_at_unix_ms, created_at
		 FROM task_events
		 WHERE task_id = $1 AND id > $2
		 ORDER BY id ASC
		 LIMIT $3`,
		taskID,
		afterEventID,
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	events := make([]domain.TaskEvent, 0)
	for rows.Next() {
		var event domain.TaskEvent
		if err := rows.Scan(
			&event.ID,
			&event.TaskID,
			&event.Type,
			&event.Message,
			&event.Token,
			&event.TraceID,
			&event.EmittedAtUnixMS,
			&event.CreatedAt,
		); err != nil {
			return nil, err
		}

		events = append(events, event)
	}

	if err := rows.Err(); err != nil {
		return nil, err
	}

	// 区分“暂无事件”和“任务不存在”，供 SSE 层做正确语义处理。
	exists, err := s.taskExists(ctx, taskID)
	if err != nil {
		return nil, err
	}
	if !exists {
		return nil, ErrTaskNotFound
	}

	return events, nil
}

// MarkDeadLetter 写入或更新任务死信信息。
func (s *PostgresStore) MarkDeadLetter(taskID string, reason string, attempts int) error {
	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	_, err := s.db.ExecContext(
		ctx,
		`INSERT INTO dead_letter_tasks (task_id, reason, attempts)
		 VALUES ($1, $2, $3)
		 ON CONFLICT (task_id)
		 DO UPDATE SET reason = EXCLUDED.reason, attempts = EXCLUDED.attempts, updated_at = NOW()`,
		taskID,
		reason,
		attempts,
	)
	if err != nil {
		var pqErr *pq.Error
		if errors.As(err, &pqErr) && pqErr.Code == "23503" {
			// 外键约束冲突：未知任务不能写入死信。
			return ErrTaskNotFound
		}
		return err
	}

	return nil
}

// ClearDeadLetter 清理指定任务的死信记录（存在则删除）。
func (s *PostgresStore) ClearDeadLetter(taskID string) error {
	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	_, err := s.db.ExecContext(ctx, `DELETE FROM dead_letter_tasks WHERE task_id = $1`, taskID)
	return err
}

// ListDeadLetters 返回按更新时间倒序排列的死信任务。
func (s *PostgresStore) ListDeadLetters(limit int) ([]domain.DeadLetterTask, error) {
	if limit <= 0 {
		limit = 100
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	rows, err := s.db.QueryContext(
		ctx,
		`SELECT task_id, reason, attempts, created_at, updated_at
		 FROM dead_letter_tasks
		 ORDER BY updated_at DESC
		 LIMIT $1`,
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	entries := make([]domain.DeadLetterTask, 0)
	for rows.Next() {
		var entry domain.DeadLetterTask
		if err := rows.Scan(&entry.TaskID, &entry.Reason, &entry.Attempts, &entry.CreatedAt, &entry.UpdatedAt); err != nil {
			return nil, err
		}
		entries = append(entries, entry)
	}

	if err := rows.Err(); err != nil {
		return nil, err
	}

	return entries, nil
}

// UpsertSystemUser 创建或更新系统用户（通常用于管理员种子账号）。
func (s *PostgresStore) UpsertSystemUser(username string, passwordHash string, role domain.UserRole) error {
	normalized := normalizeAuthUsername(username)
	if normalized == "" {
		return errors.New("username is required")
	}
	if strings.TrimSpace(passwordHash) == "" {
		return errors.New("password hash is required")
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	_, err := s.db.ExecContext(
		ctx,
		`INSERT INTO auth_users (username, password_hash, role)
		 VALUES ($1, $2, $3)
		 ON CONFLICT (username)
		 DO UPDATE SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, updated_at = NOW()`,
		normalized,
		passwordHash,
		string(role),
	)

	return err
}

// CreateUser 创建普通用户。
func (s *PostgresStore) CreateUser(user domain.AuthUser) error {
	normalized := normalizeAuthUsername(user.Username)
	if normalized == "" {
		return errors.New("username is required")
	}
	if strings.TrimSpace(user.PasswordHash) == "" {
		return errors.New("password hash is required")
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	_, err := s.db.ExecContext(
		ctx,
		`INSERT INTO auth_users (username, password_hash, role)
		 VALUES ($1, $2, $3)`,
		normalized,
		user.PasswordHash,
		string(user.Role),
	)
	if err != nil {
		var pqErr *pq.Error
		if errors.As(err, &pqErr) && pqErr.Code == "23505" {
			return ErrUserAlreadyExists
		}
		return err
	}

	return nil
}

// GetUserByUsername 按用户名查询用户。
func (s *PostgresStore) GetUserByUsername(username string) (domain.AuthUser, bool, error) {
	normalized := normalizeAuthUsername(username)
	if normalized == "" {
		return domain.AuthUser{}, false, nil
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	row := s.db.QueryRowContext(
		ctx,
		`SELECT username, password_hash, role, created_at, updated_at
		 FROM auth_users
		 WHERE username = $1`,
		normalized,
	)

	var user domain.AuthUser
	var role string
	err := row.Scan(&user.Username, &user.PasswordHash, &role, &user.CreatedAt, &user.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.AuthUser{}, false, nil
	}
	if err != nil {
		return domain.AuthUser{}, false, err
	}

	user.Role = domain.UserRole(role)
	return user, true, nil
}

// CreateSession 创建登录会话。
func (s *PostgresStore) CreateSession(session domain.AuthSession) error {
	if strings.TrimSpace(session.Token) == "" {
		return errors.New("session token is required")
	}

	normalizedUsername := normalizeAuthUsername(session.Username)
	if normalizedUsername == "" {
		return errors.New("username is required")
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	_, err := s.db.ExecContext(
		ctx,
		`INSERT INTO auth_sessions (token, username, role, expires_at)
		 VALUES ($1, $2, $3, $4)`,
		session.Token,
		normalizedUsername,
		string(session.Role),
		session.ExpiresAt,
	)

	return err
}

// GetSession 查询有效会话。
func (s *PostgresStore) GetSession(token string) (domain.AuthSession, bool, error) {
	normalizedToken := strings.TrimSpace(token)
	if normalizedToken == "" {
		return domain.AuthSession{}, false, nil
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	row := s.db.QueryRowContext(
		ctx,
		`SELECT token, username, role, expires_at, created_at
		 FROM auth_sessions
		 WHERE token = $1 AND expires_at > NOW()`,
		normalizedToken,
	)

	var session domain.AuthSession
	var role string
	err := row.Scan(&session.Token, &session.Username, &role, &session.ExpiresAt, &session.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.AuthSession{}, false, nil
	}
	if err != nil {
		return domain.AuthSession{}, false, err
	}

	session.Role = domain.UserRole(role)
	return session, true, nil
}

// DeleteSession 删除指定 token 会话。
func (s *PostgresStore) DeleteSession(token string) error {
	normalizedToken := strings.TrimSpace(token)
	if normalizedToken == "" {
		return nil
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	_, err := s.db.ExecContext(ctx, `DELETE FROM auth_sessions WHERE token = $1`, normalizedToken)
	return err
}

// DeleteSessionsByUsername 删除某用户全部会话。
func (s *PostgresStore) DeleteSessionsByUsername(username string) error {
	normalizedUsername := normalizeAuthUsername(username)
	if normalizedUsername == "" {
		return nil
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	_, err := s.db.ExecContext(ctx, `DELETE FROM auth_sessions WHERE username = $1`, normalizedUsername)
	return err
}

// DeleteExpiredSessions 清理过期会话。
func (s *PostgresStore) DeleteExpiredSessions(now time.Time) error {
	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	_, err := s.db.ExecContext(ctx, `DELETE FROM auth_sessions WHERE expires_at <= $1`, now)
	return err
}

// taskExists 是轻量存在性检查，供 ListEvents 语义判断使用。
func (s *PostgresStore) taskExists(ctx context.Context, taskID string) (bool, error) {
	var one int
	err := s.db.QueryRowContext(ctx, `SELECT 1 FROM tasks WHERE id = $1`, taskID).Scan(&one)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}

	return true, nil
}

// ensureSchema 在启动时创建必要表与索引，降低当前阶段的迁移依赖。
func (s *PostgresStore) ensureSchema(ctx context.Context) error {
	_, err := s.db.ExecContext(
		ctx,
		`CREATE TABLE IF NOT EXISTS tasks (
		 id TEXT PRIMARY KEY,
		 user_id TEXT NOT NULL,
		 prompt TEXT NOT NULL,
		 status TEXT NOT NULL,
		 error TEXT NOT NULL DEFAULT '',
		 metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
		 created_at TIMESTAMPTZ NOT NULL,
		 updated_at TIMESTAMPTZ NOT NULL
		);

		CREATE TABLE IF NOT EXISTS task_events (
		 id BIGSERIAL PRIMARY KEY,
		 task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
		 event_type TEXT NOT NULL,
		 message TEXT NOT NULL DEFAULT '',
		 token TEXT NOT NULL DEFAULT '',
		 trace_id TEXT NOT NULL DEFAULT '',
		 emitted_at_unix_ms BIGINT NOT NULL,
		 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		);

		CREATE TABLE IF NOT EXISTS dead_letter_tasks (
		 task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
		 reason TEXT NOT NULL,
		 attempts INTEGER NOT NULL,
		 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
		 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		);

		CREATE TABLE IF NOT EXISTS auth_users (
		 username TEXT PRIMARY KEY,
		 password_hash TEXT NOT NULL,
		 role TEXT NOT NULL,
		 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
		 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		);

		CREATE TABLE IF NOT EXISTS auth_sessions (
		 token TEXT PRIMARY KEY,
		 username TEXT NOT NULL REFERENCES auth_users(username) ON DELETE CASCADE,
		 role TEXT NOT NULL,
		 expires_at TIMESTAMPTZ NOT NULL,
		 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		);

		CREATE INDEX IF NOT EXISTS idx_task_events_task_id_id ON task_events (task_id, id);
		CREATE INDEX IF NOT EXISTS idx_tasks_user_conversation_created
		 ON tasks (user_id, (metadata->>'conversation_id'), created_at DESC);
		CREATE INDEX IF NOT EXISTS idx_auth_sessions_username ON auth_sessions (username);
		CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires_at ON auth_sessions (expires_at);`,
	)
	return err
}

// rowScanner 抽象 sql.Row 与 sql.Rows，使 scanTask 可复用。
type rowScanner interface {
	Scan(dest ...any) error
}

// scanTask 将 SQL 行映射为 domain.Task，并规范化 metadata 字段。
func scanTask(scanner rowScanner) (domain.Task, error) {
	var task domain.Task
	var metadataRaw []byte
	var status string

	if err := scanner.Scan(
		&task.ID,
		&task.UserID,
		&task.Prompt,
		&status,
		&task.Error,
		&metadataRaw,
		&task.CreatedAt,
		&task.UpdatedAt,
	); err != nil {
		return domain.Task{}, err
	}

	task.Status = domain.TaskStatus(status)
	if len(metadataRaw) > 0 {
		if err := json.Unmarshal(metadataRaw, &task.Metadata); err != nil {
			return domain.Task{}, err
		}
	}
	if task.Metadata == nil {
		task.Metadata = map[string]string{}
	}

	return task, nil
}
