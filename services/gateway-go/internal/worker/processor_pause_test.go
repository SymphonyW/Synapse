package worker

import (
	"context"
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/agentinfo"
	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
)

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
					Message:         `{"agent_event":"approval_required","payload":{"resume_step_index":2,"tool":"http_api","tool_input":"https://example.com/api","risk_level":"high","approval_reason":"external api requires approval"}}`,
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
	if task.Metadata[metadataAgentRequiredToolInputKey] != "https://example.com/api" {
		t.Fatalf("unexpected required tool input metadata: got %q", task.Metadata[metadataAgentRequiredToolInputKey])
	}
	if task.Metadata[metadataAgentRequiredToolRiskKey] != "high" {
		t.Fatalf("unexpected required tool risk metadata: got %q", task.Metadata[metadataAgentRequiredToolRiskKey])
	}
	if task.Metadata[metadataAgentRequiredReasonKey] != "external api requires approval" {
		t.Fatalf("unexpected required reason metadata: got %q", task.Metadata[metadataAgentRequiredReasonKey])
	}
	if task.Metadata[metadataApprovalGrantedKey] != "false" {
		t.Fatalf("unexpected approval flag metadata: got %q", task.Metadata[metadataApprovalGrantedKey])
	}

	events, err := taskStore.ListEvents("task-pause-approval", 0, 20)
	if err != nil {
		t.Fatalf("ListEvents returned error: %v", err)
	}
	assertPausedEventPresent(t, events)
}

func TestProcessWithRetryPausesTaskOnTypedApprovalRequired(t *testing.T) {
	taskStore := store.NewInMemory()
	seedWorkerTask(t, taskStore, "task-pause-typed-approval", domain.TaskQueued, "")

	now := time.Now().UTC().UnixMilli()
	agentClient := &fakeAgentClient{
		submitTask: func(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
			return newScriptedSubmitTaskStream(ctx, []*agentv1.AgentEvent{
				{Type: agentv1.AgentEventType_AGENT_EVENT_TYPE_STARTED, Message: "task started", EmittedAtUnixMs: now},
				{
					Type:          agentv1.AgentEventType_AGENT_EVENT_TYPE_INFO,
					SchemaVersion: agentinfo.SchemaV2,
					TypedPayload: &agentv1.AgentEvent_ApprovalRequired{
						ApprovalRequired: &agentv1.ApprovalRequiredEvent{
							StepIndex:       3,
							ResumeStepIndex: 3,
							ToolName:        "browser_fetch",
							ToolInput:       "https://example.com/report",
							RiskLevel:       "high",
							Reason:          "approval_required",
							ApprovalReason:  "external browser access requires approval",
							ToolCallId:      "call-typed-approval",
						},
					},
					EmittedAtUnixMs: now + 1,
				},
			}), nil
		},
	}

	processor := NewTaskProcessor(taskStore, queue.NewInMemoryQueue(4), agentClient, ProcessorOptions{
		MaxAttempts:  1,
		RetryBackoff: 5 * time.Millisecond,
	})
	processor.processWithRetry(context.Background(), "task-pause-typed-approval")

	task, ok := taskStore.Get("task-pause-typed-approval")
	if !ok {
		t.Fatal("task not found")
	}
	if task.Status != domain.TaskPaused {
		t.Fatalf("unexpected status: got %q want %q", task.Status, domain.TaskPaused)
	}
	if task.Metadata[metadataAgentResumeStepKey] != "3" {
		t.Fatalf("unexpected resume step metadata: got %q", task.Metadata[metadataAgentResumeStepKey])
	}
	if task.Metadata[metadataAgentRequiredToolKey] != "browser_fetch" {
		t.Fatalf("unexpected required tool metadata: got %q", task.Metadata[metadataAgentRequiredToolKey])
	}
	if task.Metadata[metadataAgentRequiredToolInputKey] != "https://example.com/report" {
		t.Fatalf("unexpected required tool input metadata: got %q", task.Metadata[metadataAgentRequiredToolInputKey])
	}
	if task.Metadata[metadataAgentRequiredReasonKey] != "external browser access requires approval" {
		t.Fatalf("unexpected required reason metadata: got %q", task.Metadata[metadataAgentRequiredReasonKey])
	}

	events, err := taskStore.ListEvents("task-pause-typed-approval", 0, 20)
	if err != nil {
		t.Fatalf("ListEvents returned error: %v", err)
	}
	assertPausedEventPresent(t, events)
	for _, event := range events {
		if event.EventName != "approval_required" {
			continue
		}
		if event.SchemaVersion != agentinfo.SchemaV2 {
			t.Fatalf("unexpected schema version: got %q", event.SchemaVersion)
		}
		if event.Payload["tool"] != "browser_fetch" {
			t.Fatalf("unexpected persisted typed payload: %#v", event.Payload)
		}
		return
	}
	t.Fatalf("expected persisted typed approval info event, got %#v", events)
}

func TestProcessWithRetryPersistsStandardToolInfoEventVerbatim(t *testing.T) {
	taskStore := store.NewInMemory()
	seedWorkerTask(t, taskStore, "task-tool-info-raw", domain.TaskQueued, "")

	now := time.Now().UTC().UnixMilli()
	rawToolEvent := `{"schema":"synapse.agent.info.v1","agent_event":"tool_finished","payload":{"step_index":1,"tool":"calculator","tool_input":"8 * 9","ok":true,"output":"calculator result: 72"},"display_message":"Tool finished: calculator"}`
	agentClient := &fakeAgentClient{
		submitTask: func(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
			return newScriptedSubmitTaskStream(ctx, []*agentv1.AgentEvent{
				{Type: agentv1.AgentEventType_AGENT_EVENT_TYPE_STARTED, Message: "task started", EmittedAtUnixMs: now},
				{
					Type:            agentv1.AgentEventType_AGENT_EVENT_TYPE_INFO,
					Message:         rawToolEvent,
					EmittedAtUnixMs: now + 1,
				},
				{Type: agentv1.AgentEventType_AGENT_EVENT_TYPE_COMPLETED, Message: "task completed", EmittedAtUnixMs: now + 2},
			}), nil
		},
	}

	processor := NewTaskProcessor(taskStore, queue.NewInMemoryQueue(4), agentClient, ProcessorOptions{
		MaxAttempts:  1,
		RetryBackoff: 5 * time.Millisecond,
	})
	processor.processWithRetry(context.Background(), "task-tool-info-raw")

	events, err := taskStore.ListEvents("task-tool-info-raw", 0, 20)
	if err != nil {
		t.Fatalf("ListEvents returned error: %v", err)
	}

	for _, event := range events {
		if event.Type == "info" && event.Message == rawToolEvent {
			if event.EventName != "tool_finished" {
				t.Fatalf("unexpected parsed event name: got %q", event.EventName)
			}
			return
		}
	}

	t.Fatalf("expected raw tool info event to be persisted verbatim, got %#v", events)
}

func TestProcessWithRetryAcceptsMixedLegacyAndTypedInfoEvents(t *testing.T) {
	taskStore := store.NewInMemory()
	seedWorkerTask(t, taskStore, "task-mixed-info", domain.TaskQueued, "")

	now := time.Now().UTC().UnixMilli()
	legacyPlan := `{"schema":"synapse.agent.info.v1","agent_event":"plan","payload":{"step_count":1,"steps":["calculate"]}}`
	agentClient := &fakeAgentClient{
		submitTask: func(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
			return newScriptedSubmitTaskStream(ctx, []*agentv1.AgentEvent{
				{Type: agentv1.AgentEventType_AGENT_EVENT_TYPE_STARTED, Message: "task started", EmittedAtUnixMs: now},
				{Type: agentv1.AgentEventType_AGENT_EVENT_TYPE_INFO, Message: legacyPlan, EmittedAtUnixMs: now + 1},
				{
					Type:          agentv1.AgentEventType_AGENT_EVENT_TYPE_INFO,
					SchemaVersion: agentinfo.SchemaV2,
					TypedPayload: &agentv1.AgentEvent_ToolFinished{
						ToolFinished: &agentv1.ToolFinishedEvent{
							StepIndex:     1,
							Objective:     "calculate",
							ToolName:      "calculator",
							ToolCallId:    "call-1",
							RiskLevel:     "low",
							InputPreview:  "8 * 9",
							OutputPreview: "calculator result: 72",
							DurationMs:    7,
							Ok:            true,
							ProviderName:  "builtin",
						},
					},
					EmittedAtUnixMs: now + 2,
				},
				{Type: agentv1.AgentEventType_AGENT_EVENT_TYPE_COMPLETED, Message: "task completed", EmittedAtUnixMs: now + 3},
			}), nil
		},
	}

	processor := NewTaskProcessor(taskStore, queue.NewInMemoryQueue(4), agentClient, ProcessorOptions{
		MaxAttempts:  1,
		RetryBackoff: 5 * time.Millisecond,
	})
	processor.processWithRetry(context.Background(), "task-mixed-info")

	task, ok := taskStore.Get("task-mixed-info")
	if !ok {
		t.Fatal("task not found")
	}
	if task.Status != domain.TaskCompleted {
		t.Fatalf("unexpected task status: got %q want %q", task.Status, domain.TaskCompleted)
	}

	events, err := taskStore.ListEvents("task-mixed-info", 0, 20)
	if err != nil {
		t.Fatalf("ListEvents returned error: %v", err)
	}

	seenLegacy := false
	seenTyped := false
	for _, event := range events {
		switch event.EventName {
		case "plan":
			seenLegacy = event.SchemaVersion == agentinfo.SchemaLegacyInfo
		case "tool_finished":
			seenTyped = event.SchemaVersion == agentinfo.SchemaV2 &&
				event.Payload["tool"] == "calculator" &&
				event.Payload["output_preview"] == "calculator result: 72"
		}
	}
	if !seenLegacy || !seenTyped {
		t.Fatalf("expected mixed legacy and typed info events, got %#v", events)
	}
}

func assertPausedEventPresent(t *testing.T, events []domain.TaskEvent) {
	t.Helper()
	for _, event := range events {
		if event.Type == "paused" {
			return
		}
	}
	t.Fatalf("expected paused event, got %#v", events)
}
