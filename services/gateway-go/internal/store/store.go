package store

import (
	"errors"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
)

// ErrTaskNotFound 表示目标任务不存在。
var ErrTaskNotFound = errors.New("task not found")

// ErrUserAlreadyExists 表示用户名已存在。
var ErrUserAlreadyExists = errors.New("user already exists")

// TaskStore 定义任务、事件、死信记录的持久化抽象。
type TaskStore interface {
	// Create 写入新任务。
	Create(task domain.Task) error
	// Get 按 ID 查询任务。
	Get(taskID string) (domain.Task, bool)
	// ListTasks 按更新时间倒序列出任务，可按状态过滤。
	ListTasks(limit int, status string) ([]domain.Task, error)
	// ListTasksByConversation 按用户和会话读取历史任务。
	ListTasksByConversation(userID string, conversationID string, limit int) ([]domain.Task, error)
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

	// UpsertSystemUser 创建或更新系统账号（用于管理员种子用户）。
	UpsertSystemUser(username string, passwordHash string, role domain.UserRole) error
	// CreateUser 创建普通用户。
	CreateUser(user domain.AuthUser) error
	// GetUserByUsername 按用户名查询用户。
	GetUserByUsername(username string) (domain.AuthUser, bool, error)
	// CreateSession 创建登录会话。
	CreateSession(session domain.AuthSession) error
	// GetSession 查询有效会话。
	GetSession(token string) (domain.AuthSession, bool, error)
	// DeleteSession 删除会话。
	DeleteSession(token string) error
	// DeleteSessionsByUsername 删除某用户的全部会话。
	DeleteSessionsByUsername(username string) error
	// DeleteExpiredSessions 清理过期会话。
	DeleteExpiredSessions(now time.Time) error
}
