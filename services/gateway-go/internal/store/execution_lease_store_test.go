package store

import (
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
)

func TestInMemoryAcquireExecutionLeaseAllowsOnlyOneWorker(t *testing.T) {
	taskStore := NewInMemory()
	seedLeaseTask(t, taskStore, "task-lease", domain.TaskQueued)

	first, acquired, err := taskStore.AcquireExecutionLease("task-lease", "worker-a", time.Now().Add(time.Minute))
	if err != nil {
		t.Fatalf("AcquireExecutionLease returned error: %v", err)
	}
	if !acquired {
		t.Fatal("expected first worker to acquire lease")
	}
	if first.Status != domain.TaskRunning || first.ExecutionOwner != "worker-a" || first.ExecutionAttempt != 1 {
		t.Fatalf("unexpected first lease task: %#v", first)
	}

	second, acquired, err := taskStore.AcquireExecutionLease("task-lease", "worker-b", time.Now().Add(time.Minute))
	if err != nil {
		t.Fatalf("AcquireExecutionLease returned error: %v", err)
	}
	if acquired {
		t.Fatalf("expected second worker to be rejected, got %#v", second)
	}
	if second.ExecutionOwner != "worker-a" {
		t.Fatalf("unexpected lease owner after rejected acquire: %#v", second)
	}
}

func TestInMemoryAcquireExecutionLeaseReclaimsExpiredRunningTask(t *testing.T) {
	taskStore := NewInMemory()
	seedLeaseTask(t, taskStore, "task-expired-lease", domain.TaskQueued)

	if _, acquired, err := taskStore.AcquireExecutionLease("task-expired-lease", "worker-a", time.Now().Add(-time.Second)); err != nil {
		t.Fatalf("AcquireExecutionLease returned error: %v", err)
	} else if !acquired {
		t.Fatal("expected first worker to acquire lease")
	}

	reclaimed, acquired, err := taskStore.AcquireExecutionLease("task-expired-lease", "worker-b", time.Now().Add(time.Minute))
	if err != nil {
		t.Fatalf("AcquireExecutionLease returned error: %v", err)
	}
	if !acquired {
		t.Fatal("expected second worker to reclaim expired lease")
	}
	if reclaimed.ExecutionOwner != "worker-b" || reclaimed.ExecutionAttempt != 2 {
		t.Fatalf("unexpected reclaimed task: %#v", reclaimed)
	}
}

func TestInMemoryAcquireExecutionLeaseRejectsTerminalTask(t *testing.T) {
	taskStore := NewInMemory()
	seedLeaseTask(t, taskStore, "task-terminal", domain.TaskCompleted)

	task, acquired, err := taskStore.AcquireExecutionLease("task-terminal", "worker-a", time.Now().Add(time.Minute))
	if err != nil {
		t.Fatalf("AcquireExecutionLease returned error: %v", err)
	}
	if acquired {
		t.Fatalf("terminal task should not be acquired: %#v", task)
	}
	if task.Status != domain.TaskCompleted {
		t.Fatalf("unexpected task status: %q", task.Status)
	}
}

func seedLeaseTask(t *testing.T, taskStore *InMemoryStore, taskID string, status domain.TaskStatus) {
	t.Helper()
	now := time.Now().UTC()
	if err := taskStore.Create(domain.Task{
		ID:        taskID,
		UserID:    "lease-user",
		Prompt:    "lease test",
		Status:    domain.TaskQueued,
		Metadata:  map[string]string{},
		CreatedAt: now,
		UpdatedAt: now,
	}); err != nil {
		t.Fatalf("Create returned error: %v", err)
	}
	if status != domain.TaskQueued {
		if _, ok := taskStore.UpdateStatus(taskID, status, ""); !ok {
			t.Fatalf("UpdateStatus returned not found")
		}
	}
}
