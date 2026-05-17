package domain

import "time"

// TaskStatus 表示任务生命周期状态。
type TaskStatus string

const (
	// TaskQueued 任务已入队，等待 worker 消费。
	TaskQueued TaskStatus = "queued"
	// TaskRunning 任务正在执行。
	TaskRunning TaskStatus = "running"
	// TaskCompleted 任务成功完成。
	TaskCompleted TaskStatus = "completed"
	// TaskPaused 任务因等待审批而暂停。
	TaskPaused TaskStatus = "paused"
	// TaskFailed 任务执行失败。
	TaskFailed TaskStatus = "failed"
	// TaskCanceled 任务被取消。
	TaskCanceled TaskStatus = "canceled"
)

// Task 是任务主记录，包含请求输入和当前状态。
type Task struct {
	ID             string            `json:"id"`
	UserID         string            `json:"user_id"`
	Prompt         string            `json:"prompt"`
	Status         TaskStatus        `json:"status"`
	Error          string            `json:"error,omitempty"`
	ReplayOfTaskID string            `json:"replay_of_task_id,omitempty"`
	Metadata       map[string]string `json:"metadata,omitempty"`
	CreatedAt      time.Time         `json:"created_at"`
	UpdatedAt      time.Time         `json:"updated_at"`
}

// TaskEvent 是任务执行过程中的增量事件，主要用于 SSE 推送与审计。
type TaskEvent struct {
	ID              int64     `json:"id"`
	TaskID          string    `json:"task_id"`
	Type            string    `json:"type"`
	Message         string    `json:"message,omitempty"`
	Token           string    `json:"token,omitempty"`
	TraceID         string    `json:"trace_id,omitempty"`
	EmittedAtUnixMS int64     `json:"emitted_at_unix_ms"`
	CreatedAt       time.Time `json:"created_at"`
}

// DeadLetterTask 记录重试耗尽后进入死信队列的任务信息。
type DeadLetterTask struct {
	TaskID    string    `json:"task_id"`
	Reason    string    `json:"reason"`
	Attempts  int       `json:"attempts"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}
