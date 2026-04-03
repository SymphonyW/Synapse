package store

import (
	"errors"
	"sort"
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
	nextEventID int64
}

// NewInMemory 创建空的内存存储。
func NewInMemory() *InMemoryStore {
	return &InMemoryStore{
		tasks:       map[string]domain.Task{},
		events:      map[string][]domain.TaskEvent{},
		deadLetters: map[string]domain.DeadLetterTask{},
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

// cloneEvent 目前事件是值类型，直接返回即可；保留该函数便于未来扩展。
func cloneEvent(event domain.TaskEvent) domain.TaskEvent {
	return event
}
