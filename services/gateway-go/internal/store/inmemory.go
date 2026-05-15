package store

import (
	"errors"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
)

// ErrTaskAlreadyExists 表示创建了重复 taskID。
var ErrTaskAlreadyExists = errors.New("task already exists")

// InMemoryStore 是线程安全的内存实现，适合开发和测试环境。
type InMemoryStore struct {
	mu          sync.RWMutex
	tasks       map[string]domain.Task
	events      map[string][]domain.TaskEvent
	deadLetters map[string]domain.DeadLetterTask
	toolPolicy  *domain.ToolPolicy
	users       map[string]domain.AuthUser
	sessions    map[string]domain.AuthSession
	nextEventID int64
}

// NewInMemory 创建空的内存存储。
func NewInMemory() *InMemoryStore {
	return &InMemoryStore{
		tasks:       map[string]domain.Task{},
		events:      map[string][]domain.TaskEvent{},
		deadLetters: map[string]domain.DeadLetterTask{},
		users:       map[string]domain.AuthUser{},
		sessions:    map[string]domain.AuthSession{},
	}
}

// Create 写入任务，并初始化该任务的事件列表。
func (s *InMemoryStore) Create(task domain.Task) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if _, exists := s.tasks[task.ID]; exists {
		return ErrTaskAlreadyExists
	}

	s.tasks[task.ID] = cloneTask(task)
	s.events[task.ID] = []domain.TaskEvent{}
	return nil
}

// Get 返回任务副本，避免外部修改内部状态。
func (s *InMemoryStore) Get(taskID string) (domain.Task, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	task, ok := s.tasks[taskID]
	if !ok {
		return domain.Task{}, false
	}

	return cloneTask(task), true
}

// ListTasks 读取任务列表并按更新时间倒序排序。
func (s *InMemoryStore) ListTasks(limit int, status string) ([]domain.Task, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	tasks := make([]domain.Task, 0, len(s.tasks))
	for _, task := range s.tasks {
		if status != "" && string(task.Status) != status {
			continue
		}
		tasks = append(tasks, cloneTask(task))
	}

	sort.Slice(tasks, func(i, j int) bool {
		left := tasks[i]
		right := tasks[j]
		if left.UpdatedAt.Equal(right.UpdatedAt) {
			return left.CreatedAt.After(right.CreatedAt)
		}
		return left.UpdatedAt.After(right.UpdatedAt)
	})

	if limit > 0 && len(tasks) > limit {
		tasks = tasks[:limit]
	}

	return tasks, nil
}

// ListTasksByConversation 按用户和会话读取历史任务，按创建时间升序返回。
func (s *InMemoryStore) ListTasksByConversation(userID string, conversationID string, limit int) ([]domain.Task, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	trimmedUserID := strings.TrimSpace(userID)
	trimmedConversationID := strings.TrimSpace(conversationID)
	if trimmedUserID == "" || trimmedConversationID == "" {
		return []domain.Task{}, nil
	}

	tasks := make([]domain.Task, 0)
	for _, task := range s.tasks {
		if task.UserID != trimmedUserID {
			continue
		}

		if !matchesConversationTask(task, trimmedConversationID) {
			continue
		}

		tasks = append(tasks, cloneTask(task))
	}

	sort.Slice(tasks, func(i, j int) bool {
		left := tasks[i]
		right := tasks[j]
		if left.CreatedAt.Equal(right.CreatedAt) {
			return left.UpdatedAt.Before(right.UpdatedAt)
		}
		return left.CreatedAt.Before(right.CreatedAt)
	})

	if limit > 0 && len(tasks) > limit {
		tasks = tasks[len(tasks)-limit:]
	}

	return tasks, nil
}

// DeleteTasksByConversation 删除某用户在指定会话下的全部任务，并清理关联事件与死信。
func (s *InMemoryStore) DeleteTasksByConversation(userID string, conversationID string) ([]string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	trimmedUserID := strings.TrimSpace(userID)
	trimmedConversationID := strings.TrimSpace(conversationID)
	if trimmedUserID == "" || trimmedConversationID == "" {
		return []string{}, nil
	}

	deletedTaskIDs := make([]string, 0)
	for taskID, task := range s.tasks {
		if task.UserID != trimmedUserID {
			continue
		}

		if !matchesConversationTask(task, trimmedConversationID) {
			continue
		}

		delete(s.tasks, taskID)
		delete(s.events, taskID)
		delete(s.deadLetters, taskID)
		deletedTaskIDs = append(deletedTaskIDs, taskID)
	}

	sort.Strings(deletedTaskIDs)
	return deletedTaskIDs, nil
}

func matchesConversationTask(task domain.Task, conversationID string) bool {
	metadataConversationID := strings.TrimSpace(task.Metadata["conversation_id"])
	if metadataConversationID != "" {
		return metadataConversationID == conversationID
	}

	// 兼容历史数据：早期会话没有 conversation_id 时，前端按 task.id 作为会话键。
	return strings.TrimSpace(task.ID) == conversationID
}

// UpdateStatus 更新任务状态和错误信息。
func (s *InMemoryStore) UpdateStatus(taskID string, status domain.TaskStatus, errorMessage string) (domain.Task, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	task, ok := s.tasks[taskID]
	if !ok {
		return domain.Task{}, false
	}

	task.Status = status
	task.Error = errorMessage
	task.UpdatedAt = time.Now().UTC()
	s.tasks[taskID] = task

	return cloneTask(task), true
}

// UpdateMetadata 合并更新任务 metadata，空值表示删除对应 key。
func (s *InMemoryStore) UpdateMetadata(taskID string, metadataUpdates map[string]string) (domain.Task, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	task, ok := s.tasks[taskID]
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

	task.UpdatedAt = time.Now().UTC()
	s.tasks[taskID] = task

	return cloneTask(task), true, nil
}

// AppendEvent 为任务追加事件并分配递增事件 ID。
func (s *InMemoryStore) AppendEvent(taskID string, event domain.TaskEvent) (domain.TaskEvent, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if _, ok := s.tasks[taskID]; !ok {
		return domain.TaskEvent{}, ErrTaskNotFound
	}

	s.nextEventID++
	event.ID = s.nextEventID
	event.TaskID = taskID
	if event.CreatedAt.IsZero() {
		event.CreatedAt = time.Now().UTC()
	}
	if event.EmittedAtUnixMS == 0 {
		event.EmittedAtUnixMS = event.CreatedAt.UnixMilli()
	}

	s.events[taskID] = append(s.events[taskID], cloneEvent(event))
	return cloneEvent(event), nil
}

// ListEvents 按事件 ID 增量返回事件。
func (s *InMemoryStore) ListEvents(taskID string, afterEventID int64, limit int) ([]domain.TaskEvent, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if _, ok := s.tasks[taskID]; !ok {
		return nil, ErrTaskNotFound
	}

	allEvents := s.events[taskID]
	result := make([]domain.TaskEvent, 0, len(allEvents))
	for _, event := range allEvents {
		if event.ID <= afterEventID {
			continue
		}

		result = append(result, cloneEvent(event))
		if limit > 0 && len(result) >= limit {
			break
		}
	}

	return result, nil
}

// MarkDeadLetter 写入或更新死信信息。
func (s *InMemoryStore) MarkDeadLetter(taskID string, reason string, attempts int) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if _, ok := s.tasks[taskID]; !ok {
		return ErrTaskNotFound
	}

	now := time.Now().UTC()
	entry, exists := s.deadLetters[taskID]
	if !exists {
		entry = domain.DeadLetterTask{
			TaskID:    taskID,
			CreatedAt: now,
		}
	}

	entry.Reason = reason
	entry.Attempts = attempts
	entry.UpdatedAt = now
	if entry.CreatedAt.IsZero() {
		entry.CreatedAt = now
	}
	s.deadLetters[taskID] = entry
	return nil
}

// ClearDeadLetter 清理指定任务的死信记录。
func (s *InMemoryStore) ClearDeadLetter(taskID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	delete(s.deadLetters, taskID)
	return nil
}

// ListDeadLetters 返回按更新时间倒序排列的死信列表。
func (s *InMemoryStore) ListDeadLetters(limit int) ([]domain.DeadLetterTask, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	entries := make([]domain.DeadLetterTask, 0, len(s.deadLetters))
	for _, entry := range s.deadLetters {
		entries = append(entries, entry)
	}

	sort.Slice(entries, func(i, j int) bool {
		return entries[i].UpdatedAt.After(entries[j].UpdatedAt)
	})

	if limit > 0 && len(entries) > limit {
		entries = entries[:limit]
	}

	return entries, nil
}

func (s *InMemoryStore) GetToolPolicy() (domain.ToolPolicy, bool, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if s.toolPolicy == nil {
		return domain.ToolPolicy{}, false, nil
	}
	return cloneToolPolicy(*s.toolPolicy), true, nil
}

func (s *InMemoryStore) UpsertToolPolicy(policy domain.ToolPolicy) (domain.ToolPolicy, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	cloned := cloneToolPolicy(policy)
	s.toolPolicy = &cloned
	return cloneToolPolicy(cloned), nil
}

// UpsertSystemUser 创建或更新系统账号（通常用于管理员种子用户）。
func (s *InMemoryStore) UpsertSystemUser(username string, passwordHash string, role domain.UserRole) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	normalized := normalizeAuthUsername(username)
	if normalized == "" {
		return errors.New("username is required")
	}
	if passwordHash == "" {
		return errors.New("password hash is required")
	}

	now := time.Now().UTC()
	entry, exists := s.users[normalized]
	if !exists {
		entry = domain.AuthUser{
			Username:  normalized,
			CreatedAt: now,
		}
	}

	entry.Username = normalized
	entry.PasswordHash = passwordHash
	entry.Role = role
	entry.UpdatedAt = now
	if entry.CreatedAt.IsZero() {
		entry.CreatedAt = now
	}

	s.users[normalized] = entry
	return nil
}

// CreateUser 创建普通用户。
func (s *InMemoryStore) CreateUser(user domain.AuthUser) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	normalized := normalizeAuthUsername(user.Username)
	if normalized == "" {
		return errors.New("username is required")
	}
	if user.PasswordHash == "" {
		return errors.New("password hash is required")
	}

	if _, exists := s.users[normalized]; exists {
		return ErrUserAlreadyExists
	}

	now := time.Now().UTC()
	entry := domain.AuthUser{
		Username:     normalized,
		PasswordHash: user.PasswordHash,
		Role:         user.Role,
		CreatedAt:    now,
		UpdatedAt:    now,
	}

	s.users[normalized] = entry
	return nil
}

// GetUserByUsername 按用户名读取用户。
func (s *InMemoryStore) GetUserByUsername(username string) (domain.AuthUser, bool, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	normalized := normalizeAuthUsername(username)
	if normalized == "" {
		return domain.AuthUser{}, false, nil
	}

	user, exists := s.users[normalized]
	if !exists {
		return domain.AuthUser{}, false, nil
	}

	return user, true, nil
}

// CreateSession 创建登录会话。
func (s *InMemoryStore) CreateSession(session domain.AuthSession) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if strings.TrimSpace(session.Token) == "" {
		return errors.New("session token is required")
	}

	normalized := normalizeAuthUsername(session.Username)
	if normalized == "" {
		return errors.New("username is required")
	}

	now := time.Now().UTC()
	entry := session
	entry.Username = normalized
	if entry.CreatedAt.IsZero() {
		entry.CreatedAt = now
	}

	s.sessions[entry.Token] = entry
	return nil
}

// GetSession 查询会话，并在读取时剔除过期会话。
func (s *InMemoryStore) GetSession(token string) (domain.AuthSession, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	normalizedToken := strings.TrimSpace(token)
	if normalizedToken == "" {
		return domain.AuthSession{}, false, nil
	}

	session, exists := s.sessions[normalizedToken]
	if !exists {
		return domain.AuthSession{}, false, nil
	}

	if !session.ExpiresAt.IsZero() && !session.ExpiresAt.After(time.Now().UTC()) {
		delete(s.sessions, normalizedToken)
		return domain.AuthSession{}, false, nil
	}

	return session, true, nil
}

// DeleteSession 删除指定 token 会话。
func (s *InMemoryStore) DeleteSession(token string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	delete(s.sessions, strings.TrimSpace(token))
	return nil
}

// DeleteSessionsByUsername 删除某用户全部会话。
func (s *InMemoryStore) DeleteSessionsByUsername(username string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	normalized := normalizeAuthUsername(username)
	if normalized == "" {
		return nil
	}

	for token, session := range s.sessions {
		if normalizeAuthUsername(session.Username) == normalized {
			delete(s.sessions, token)
		}
	}

	return nil
}

// DeleteExpiredSessions 清理过期会话。
func (s *InMemoryStore) DeleteExpiredSessions(now time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	for token, session := range s.sessions {
		if !session.ExpiresAt.IsZero() && !session.ExpiresAt.After(now) {
			delete(s.sessions, token)
		}
	}

	return nil
}

func normalizeAuthUsername(value string) string {
	return strings.ToLower(strings.TrimSpace(value))
}

// cloneTask 深拷贝任务，防止 metadata map 共享。
func cloneTask(task domain.Task) domain.Task {
	copyTask := task
	if task.Metadata == nil {
		return copyTask
	}

	copyTask.Metadata = make(map[string]string, len(task.Metadata))
	for key, value := range task.Metadata {
		copyTask.Metadata[key] = value
	}

	return copyTask
}

func cloneToolPolicy(policy domain.ToolPolicy) domain.ToolPolicy {
	copyPolicy := policy
	if policy.RoleAllow != nil {
		copyPolicy.RoleAllow = make(map[string][]string, len(policy.RoleAllow))
		for role, tools := range policy.RoleAllow {
			copyPolicy.RoleAllow[role] = append([]string{}, tools...)
		}
	}
	copyPolicy.ApprovalRequired = append([]string{}, policy.ApprovalRequired...)
	copyPolicy.DisabledTools = append([]string{}, policy.DisabledTools...)
	return copyPolicy
}

// cloneEvent 目前事件是值类型，直接返回即可；保留该函数便于未来扩展。
func cloneEvent(event domain.TaskEvent) domain.TaskEvent {
	return event
}
