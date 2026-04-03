package worker

import (
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
)

// 验证 finalizeCanceled 会保留已有取消原因并清理死信记录。
func TestFinalizeCanceledKeepsExistingReasonAndClearsDeadLetter(t *testing.T) {
	taskStore := store.NewInMemory()
	seedWorkerTask(t, taskStore, "task-keep-reason", domain.TaskCanceled, "canceled by ops-console: batch maintenance")
	if err := taskStore.MarkDeadLetter("task-keep-reason", "transient failure", 3); err != nil {
		t.Fatalf("MarkDeadLetter returned error: %v", err)
	}

	processor := NewTaskProcessor(taskStore, queue.NewInMemoryQueue(4), nil, ProcessorOptions{})
	processor.finalizeCanceled("task-keep-reason")

	task, ok := taskStore.Get("task-keep-reason")
	if !ok {
		t.Fatal("task not found")
	}

	if task.Status != domain.TaskCanceled {
		t.Fatalf("unexpected task status: got %q want %q", task.Status, domain.TaskCanceled)
	}
	const expectedError = "canceled by ops-console: batch maintenance"
	if task.Error != expectedError {
		t.Fatalf("unexpected cancel reason: got %q want %q", task.Error, expectedError)
	}

	deadLetters, err := taskStore.ListDeadLetters(10)
	if err != nil {
		t.Fatalf("ListDeadLetters returned error: %v", err)
	}
	if len(deadLetters) != 0 {
		t.Fatalf("dead letters should be cleared, got %#v", deadLetters)
	}

	events, err := taskStore.ListEvents("task-keep-reason", 0, 10)
	if err != nil {
		t.Fatalf("ListEvents returned error: %v", err)
	}
	if len(events) != 1 {
		t.Fatalf("unexpected event count: got %d want 1", len(events))
	}
	if events[0].Type != "canceled" {
		t.Fatalf("unexpected event type: got %q want %q", events[0].Type, "canceled")
	}
}

// 验证当任务错误信息为空时，使用默认取消原因。
func TestFinalizeCanceledUsesDefaultReasonWhenEmpty(t *testing.T) {
	taskStore := store.NewInMemory()
	seedWorkerTask(t, taskStore, "task-default-reason", domain.TaskCanceled, "")

	processor := NewTaskProcessor(taskStore, queue.NewInMemoryQueue(4), nil, ProcessorOptions{})
	processor.finalizeCanceled("task-default-reason")

	task, ok := taskStore.Get("task-default-reason")
	if !ok {
		t.Fatal("task not found")
	}

	if task.Error != "canceled by user" {
		t.Fatalf("unexpected default cancel reason: got %q", task.Error)
	}
}

// 构造 worker 测试使用的任务数据。
func seedWorkerTask(t *testing.T, taskStore *store.InMemoryStore, taskID string, status domain.TaskStatus, errMessage string) {
	t.Helper()

	now := time.Now().UTC()
	if err := taskStore.Create(domain.Task{
		ID:        taskID,
		UserID:    "worker-test-user",
		Prompt:    "worker test prompt",
		Status:    domain.TaskQueued,
		Metadata:  map[string]string{"origin": "worker-test"},
		CreatedAt: now,
		UpdatedAt: now,
	}); err != nil {
		t.Fatalf("Create returned error: %v", err)
	}

	if status != domain.TaskQueued || errMessage != "" {
		if _, ok := taskStore.UpdateStatus(taskID, status, errMessage); !ok {
			t.Fatalf("UpdateStatus returned not found for %s", taskID)
		}
	}
}
