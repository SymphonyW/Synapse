package store

import (
	"errors"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
)

// ErrTaskNotFound 表示目标任务不存在。
var ErrTaskNotFound = errors.New("task not found")

// TaskStore 定义任务、事件、死信记录的持久化抽象。
type TaskStore interface {
	// Create 写入新任务。
	Create(task domain.Task) error
	// Get 按 ID 查询任务。
	Get(taskID string) (domain.Task, bool)
	// ListTasks 按更新时间倒序列出任务，可按状态过滤。
	ListTasks(limit int, status string) ([]domain.Task, error)
	// UpdateStatus 更新任务状态与错误信息。
	UpdateStatus(taskID string, status domain.TaskStatus, errorMessage string) (domain.Task, bool)
	// AppendEvent 追加任务事件。
	AppendEvent(taskID string, event domain.TaskEvent) (domain.TaskEvent, error)
	// ListEvents 按事件 ID 增量读取任务事件。
	ListEvents(taskID string, afterEventID int64, limit int) ([]domain.TaskEvent, error)
	// MarkDeadLetter 标记任务为死信。
	MarkDeadLetter(taskID string, reason string, attempts int) error
	// ClearDeadLetter 清理任务死信记录。
	ClearDeadLetter(taskID string) error
	// ListDeadLetters 列出死信任务。
	ListDeadLetters(limit int) ([]domain.DeadLetterTask, error)
}
