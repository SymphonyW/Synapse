package worker

import (
	"context"
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
)

// 验证收到 approval_required 信息后任务会进入 paused 并持久化恢复检查点。
func TestProcessWithRetryPausesTaskOnApprovalRequired(t *testing.T) {
	taskStore := store.NewInMemory()
	seedWorkerTask(t, taskStore, "task-pause-approval", domain.TaskQueued, "")

	now := time.Now().UTC().UnixMilli()
	agentClient := &fakeAgentClient{
		submitTask: func(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
			return newScriptedSubmitTaskStream(ctx, []*agentv1.AgentEvent{
				{Type: agentv1.AgentEventType_AGENT_EVENT_TYPE_STARTED, Message: "task started", EmittedAtUnixMs: now},
				{
					Type:            agentv1.AgentEventType_AGENT_EVENT_TYPE_INFO,
					Message:         `{"agent_event":"approval_required","payload":{"resume_step_index":2,"tool":"http_api"}}`,
					EmittedAtUnixMs: now + 1,
				},
			}), nil
		},
	}

	processor := NewTaskProcessor(taskStore, queue.NewInMemoryQueue(4), agentClient, ProcessorOptions{
		MaxAttempts:  2,
		RetryBackoff: 5 * time.Millisecond,
	})
	processor.processWithRetry(context.Background(), "task-pause-approval")

	task, ok := taskStore.Get("task-pause-approval")
	if !ok {
		t.Fatal("task not found")
	}
	if task.Status != domain.TaskPaused {
		t.Fatalf("unexpected status: got %q want %q", task.Status, domain.TaskPaused)
	}

	if task.Metadata[metadataAgentResumeStepKey] != "2" {
		t.Fatalf("unexpected resume step metadata: got %q", task.Metadata[metadataAgentResumeStepKey])
	}
	if task.Metadata[metadataAgentRequiredToolKey] != "http_api" {
		t.Fatalf("unexpected required tool metadata: got %q", task.Metadata[metadataAgentRequiredToolKey])
	}
	if task.Metadata[metadataApprovalGrantedKey] != "false" {
		t.Fatalf("unexpected approval flag metadata: got %q", task.Metadata[metadataApprovalGrantedKey])
	}

	events, err := taskStore.ListEvents("task-pause-approval", 0, 20)
	if err != nil {
		t.Fatalf("ListEvents returned error: %v", err)
	}

	foundPaused := false
	for _, event := range events {
		if event.Type == "paused" {
			foundPaused = true
			break
		}
	}
	if !foundPaused {
		t.Fatalf("expected paused event, got %#v", events)
	}
}
