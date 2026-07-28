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
	dbmigration "github.com/synapse/synapse/services/gateway-go/internal/migration"
)

// dbOperationTimeout 为单次数据库操作设置上限，避免在数据库压力下无限阻塞。
const dbOperationTimeout = 3 * time.Second

const taskColumns = `id, user_id, prompt, status, error, replay_of_task_id, metadata, execution_owner, execution_lease_until, execution_attempt, created_at, updated_at`

// PostgresStore 是基于 PostgreSQL 的持久化 TaskStore 实现。
type PostgresStore struct {
	db *sql.DB
}

// NewPostgres 建立连接、校验可达性并确认数据库已完成版本化迁移。
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

	if err := dbmigration.CheckRequiredVersion(ctx, db); err != nil {
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
		`INSERT INTO tasks (id, user_id, prompt, status, error, replay_of_task_id, metadata, created_at, updated_at)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)`,
		task.ID,
		task.UserID,
		task.Prompt,
		string(task.Status),
		task.Error,
		nullIfBlank(task.ReplayOfTaskID),
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
		`SELECT `+taskColumns+`
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
	query := `SELECT ` + taskColumns + `
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

func (s *PostgresStore) ListReplays(taskID string, limit int) ([]domain.Task, error) {
	if limit <= 0 {
		limit = 50
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	rows, err := s.db.QueryContext(
		ctx,
		`SELECT `+taskColumns+`
		 FROM tasks
		 WHERE replay_of_task_id = $1
		 ORDER BY created_at DESC
		 LIMIT $2`,
		taskID,
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	replays := make([]domain.Task, 0)
	for rows.Next() {
		task, err := scanTask(rows)
		if err != nil {
			return nil, err
		}
		replays = append(replays, task)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	return replays, nil
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
		`SELECT `+taskColumns+`
		 FROM tasks
		 WHERE user_id = $1
		   AND (
			 metadata->>'conversation_id' = $2
			 OR (COALESCE(metadata->>'conversation_id', '') = '' AND id = $2)
		   )
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

// DeleteTasksByConversation 删除某用户在指定会话下的全部任务，返回已删除任务 ID。
func (s *PostgresStore) DeleteTasksByConversation(userID string, conversationID string) ([]string, error) {
	trimmedUserID := strings.TrimSpace(userID)
	trimmedConversationID := strings.TrimSpace(conversationID)
	if trimmedUserID == "" || trimmedConversationID == "" {
		return []string{}, nil
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	rows, err := s.db.QueryContext(
		ctx,
		`DELETE FROM tasks
		 WHERE user_id = $1
		   AND (
			 metadata->>'conversation_id' = $2
			 OR (COALESCE(metadata->>'conversation_id', '') = '' AND id = $2)
		   )
		 RETURNING id`,
		trimmedUserID,
		trimmedConversationID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	deletedTaskIDs := make([]string, 0)
	for rows.Next() {
		var taskID string
		if scanErr := rows.Scan(&taskID); scanErr != nil {
			return nil, scanErr
		}
		deletedTaskIDs = append(deletedTaskIDs, taskID)
	}

	if err := rows.Err(); err != nil {
		return nil, err
	}

	return deletedTaskIDs, nil
}

// UpdateStatus 原子更新任务状态并返回最新任务快照。
func (s *PostgresStore) UpdateStatus(taskID string, status domain.TaskStatus, errorMessage string) (domain.Task, bool) {
	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	row := s.db.QueryRowContext(
		ctx,
		`UPDATE tasks
		 SET status = $2,
		     error = $3,
		     execution_owner = CASE WHEN $2 = 'running' THEN execution_owner ELSE '' END,
		     execution_lease_until = CASE WHEN $2 = 'running' THEN execution_lease_until ELSE NULL END,
		     updated_at = NOW()
		 WHERE id = $1
		 RETURNING `+taskColumns,
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

// AcquireExecutionLease 原子获取 queued 或过期 running 任务的执行权。
func (s *PostgresStore) AcquireExecutionLease(taskID string, owner string, leaseUntil time.Time) (domain.Task, bool, error) {
	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	row := s.db.QueryRowContext(
		ctx,
		`UPDATE tasks
		 SET status = 'running',
		     error = '',
		     execution_owner = $2,
		     execution_lease_until = $3,
		     execution_attempt = execution_attempt + 1,
		     updated_at = NOW()
		 WHERE id = $1
		   AND (
		     status = 'queued'
		     OR (
		       status = 'running'
		       AND (
		         execution_owner = $2
		         OR execution_lease_until IS NULL
		         OR execution_lease_until < NOW()
		       )
		     )
		   )
		 RETURNING `+taskColumns,
		taskID,
		owner,
		leaseUntil.UTC(),
	)

	task, err := scanTask(row)
	if errors.Is(err, sql.ErrNoRows) {
		if task, ok := s.Get(taskID); ok {
			return task, false, nil
		}
		return domain.Task{}, false, nil
	}
	if err != nil {
		return domain.Task{}, false, err
	}

	return task, true, nil
}

// UpdateMetadata 合并更新任务 metadata，空值表示删除对应 key。
func (s *PostgresStore) UpdateMetadata(taskID string, metadataUpdates map[string]string) (domain.Task, bool, error) {
	if len(metadataUpdates) == 0 {
		task, ok := s.Get(taskID)
		if !ok {
			return domain.Task{}, false, nil
		}
		return task, true, nil
	}

	task, ok := s.Get(taskID)
	if !ok {
		return domain.Task{}, false, nil
	}

	if task.Metadata == nil {
		task.Metadata = map[string]string{}
	}

	for key, value := range metadataUpdates {
		trimmedKey := strings.TrimSpace(key)
		if trimmedKey == "" {
			continue
		}

		if strings.TrimSpace(value) == "" {
			delete(task.Metadata, trimmedKey)
			continue
		}

		task.Metadata[trimmedKey] = value
	}

	metadataJSON, err := json.Marshal(task.Metadata)
	if err != nil {
		return domain.Task{}, false, err
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	row := s.db.QueryRowContext(
		ctx,
		`UPDATE tasks
		 SET metadata = $2, updated_at = NOW()
		 WHERE id = $1
		 RETURNING `+taskColumns,
		taskID,
		metadataJSON,
	)

	updatedTask, scanErr := scanTask(row)
	if errors.Is(scanErr, sql.ErrNoRows) {
		return domain.Task{}, false, nil
	}
	if scanErr != nil {
		return domain.Task{}, false, scanErr
	}

	return updatedTask, true, nil
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
	if event.Payload == nil {
		event.Payload = map[string]any{}
	}
	payloadJSON, err := json.Marshal(event.Payload)
	if err != nil {
		return domain.TaskEvent{}, err
	}

	row := s.db.QueryRowContext(
		ctx,
		`INSERT INTO task_events (task_id, event_type, message, token, trace_id, schema_version, event_name, payload, emitted_at_unix_ms)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
		 RETURNING id, created_at`,
		taskID,
		event.Type,
		event.Message,
		event.Token,
		event.TraceID,
		event.SchemaVersion,
		event.EventName,
		payloadJSON,
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
		`SELECT id, task_id, event_type, message, token, trace_id, schema_version, event_name, payload, emitted_at_unix_ms, created_at
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
		var payloadRaw []byte
		if err := rows.Scan(
			&event.ID,
			&event.TaskID,
			&event.Type,
			&event.Message,
			&event.Token,
			&event.TraceID,
			&event.SchemaVersion,
			&event.EventName,
			&payloadRaw,
			&event.EmittedAtUnixMS,
			&event.CreatedAt,
		); err != nil {
			return nil, err
		}
		if len(payloadRaw) > 0 {
			if err := json.Unmarshal(payloadRaw, &event.Payload); err != nil {
				return nil, err
			}
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

func (s *PostgresStore) GetToolPolicy() (domain.ToolPolicy, bool, error) {
	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	row := s.db.QueryRowContext(
		ctx,
		`SELECT role_allow, approval_required, disabled_tools, version, updated_at, updated_by, description
		 FROM tool_policies
		 WHERE id = 'default'`,
	)

	var policy domain.ToolPolicy
	var roleAllowRaw []byte
	var approvalRaw []byte
	var disabledRaw []byte
	err := row.Scan(
		&roleAllowRaw,
		&approvalRaw,
		&disabledRaw,
		&policy.Version,
		&policy.UpdatedAt,
		&policy.UpdatedBy,
		&policy.Description,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.ToolPolicy{}, false, nil
	}
	if err != nil {
		return domain.ToolPolicy{}, false, err
	}
	if err := json.Unmarshal(roleAllowRaw, &policy.RoleAllow); err != nil {
		return domain.ToolPolicy{}, false, err
	}
	if err := json.Unmarshal(approvalRaw, &policy.ApprovalRequired); err != nil {
		return domain.ToolPolicy{}, false, err
	}
	if err := json.Unmarshal(disabledRaw, &policy.DisabledTools); err != nil {
		return domain.ToolPolicy{}, false, err
	}
	return policy, true, nil
}

func (s *PostgresStore) UpsertToolPolicy(policy domain.ToolPolicy) (domain.ToolPolicy, error) {
	roleAllowJSON, err := json.Marshal(policy.RoleAllow)
	if err != nil {
		return domain.ToolPolicy{}, err
	}
	approvalJSON, err := json.Marshal(policy.ApprovalRequired)
	if err != nil {
		return domain.ToolPolicy{}, err
	}
	disabledJSON, err := json.Marshal(policy.DisabledTools)
	if err != nil {
		return domain.ToolPolicy{}, err
	}

	ctx, cancel := context.WithTimeout(context.Background(), dbOperationTimeout)
	defer cancel()

	row := s.db.QueryRowContext(
		ctx,
		`INSERT INTO tool_policies (
		   id, role_allow, approval_required, disabled_tools, version, updated_at, updated_by, description
		 )
		 VALUES ('default', $1, $2, $3, $4, $5, $6, $7)
		 ON CONFLICT (id)
		 DO UPDATE SET
		   role_allow = EXCLUDED.role_allow,
		   approval_required = EXCLUDED.approval_required,
		   disabled_tools = EXCLUDED.disabled_tools,
		   version = EXCLUDED.version,
		   updated_at = EXCLUDED.updated_at,
		   updated_by = EXCLUDED.updated_by,
		   description = EXCLUDED.description
		 RETURNING role_allow, approval_required, disabled_tools, version, updated_at, updated_by, description`,
		roleAllowJSON,
		approvalJSON,
		disabledJSON,
		policy.Version,
		policy.UpdatedAt,
		policy.UpdatedBy,
		policy.Description,
	)

	var saved domain.ToolPolicy
	var roleAllowRaw []byte
	var approvalRaw []byte
	var disabledRaw []byte
	if err := row.Scan(
		&roleAllowRaw,
		&approvalRaw,
		&disabledRaw,
		&saved.Version,
		&saved.UpdatedAt,
		&saved.UpdatedBy,
		&saved.Description,
	); err != nil {
		return domain.ToolPolicy{}, err
	}
	if err := json.Unmarshal(roleAllowRaw, &saved.RoleAllow); err != nil {
		return domain.ToolPolicy{}, err
	}
	if err := json.Unmarshal(approvalRaw, &saved.ApprovalRequired); err != nil {
		return domain.ToolPolicy{}, err
	}
	if err := json.Unmarshal(disabledRaw, &saved.DisabledTools); err != nil {
		return domain.ToolPolicy{}, err
	}
	return saved, nil
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

// rowScanner 抽象 sql.Row 与 sql.Rows，使 scanTask 可复用。
type rowScanner interface {
	Scan(dest ...any) error
}

// scanTask 将 SQL 行映射为 domain.Task，并规范化 metadata 字段。
func scanTask(scanner rowScanner) (domain.Task, error) {
	var task domain.Task
	var metadataRaw []byte
	var status string
	var replayOfTaskID sql.NullString
	var executionOwner sql.NullString
	var executionLeaseUntil sql.NullTime

	if err := scanner.Scan(
		&task.ID,
		&task.UserID,
		&task.Prompt,
		&status,
		&task.Error,
		&replayOfTaskID,
		&metadataRaw,
		&executionOwner,
		&executionLeaseUntil,
		&task.ExecutionAttempt,
		&task.CreatedAt,
		&task.UpdatedAt,
	); err != nil {
		return domain.Task{}, err
	}

	task.Status = domain.TaskStatus(status)
	if replayOfTaskID.Valid {
		task.ReplayOfTaskID = replayOfTaskID.String
	}
	if executionOwner.Valid {
		task.ExecutionOwner = executionOwner.String
	}
	if executionLeaseUntil.Valid {
		task.ExecutionLeaseUntil = executionLeaseUntil.Time
	}
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

func nullIfBlank(value string) any {
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return nil
	}
	return trimmed
}
