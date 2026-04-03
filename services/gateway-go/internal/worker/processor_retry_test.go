package worker

import (
	"context"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// scriptedSubmitTaskStream 用预定义事件序列模拟 gRPC 流。
type scriptedSubmitTaskStream struct {
	ctx    context.Context
	events []*agentv1.AgentEvent
	index  int
}

func newScriptedSubmitTaskStream(ctx context.Context, events []*agentv1.AgentEvent) *scriptedSubmitTaskStream {
	return &scriptedSubmitTaskStream{ctx: ctx, events: events}
}

func (s *scriptedSubmitTaskStream) Recv() (*agentv1.AgentEvent, error) {
	if s.index >= len(s.events) {
		return nil, io.EOF
	}

	event := s.events[s.index]
	s.index++
	return event, nil
}

func (s *scriptedSubmitTaskStream) Header() (metadata.MD, error) {
	return metadata.MD{}, nil
}

func (s *scriptedSubmitTaskStream) Trailer() metadata.MD {
	return metadata.MD{}
}

func (s *scriptedSubmitTaskStream) CloseSend() error {
	return nil
}

func (s *scriptedSubmitTaskStream) Context() context.Context {
	return s.ctx
}

func (s *scriptedSubmitTaskStream) SendMsg(any) error {
	return nil
}

func (s *scriptedSubmitTaskStream) RecvMsg(any) error {
	return io.EOF
}

func TestProcessWithRetryDeadlineExceededFailsWithoutExtraRetries(t *testing.T) {
	taskStore := store.NewInMemory()
	seedWorkerTask(t, taskStore, "task-deadline-no-retry", domain.TaskQueued, "")

	submitCalls := 0
	agentClient := &fakeAgentClient{
		submitTask: func(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
			submitCalls++
			return nil, status.Error(codes.DeadlineExceeded, "context deadline exceeded")
		},
	}

	processor := NewTaskProcessor(taskStore, queue.NewInMemoryQueue(4), agentClient, ProcessorOptions{
		MaxAttempts:  3,
		RetryBackoff: 5 * time.Millisecond,
	})
	processor.processWithRetry(context.Background(), "task-deadline-no-retry")

	if submitCalls != 1 {
		t.Fatalf("unexpected submit calls: got %d want 1", submitCalls)
	}

	task, ok := taskStore.Get("task-deadline-no-retry")
	if !ok {
		t.Fatal("task not found")
	}
	if task.Status != domain.TaskFailed {
		t.Fatalf("unexpected task status: got %q want %q", task.Status, domain.TaskFailed)
	}

	deadLetters, err := taskStore.ListDeadLetters(10)
	if err != nil {
		t.Fatalf("ListDeadLetters returned error: %v", err)
	}
	if len(deadLetters) != 1 {
		t.Fatalf("unexpected dead letter count: got %d want 1", len(deadLetters))
	}
	if deadLetters[0].Attempts != 1 {
		t.Fatalf("unexpected dead letter attempts: got %d want 1", deadLetters[0].Attempts)
	}

	events, err := taskStore.ListEvents("task-deadline-no-retry", 0, 20)
	if err != nil {
		t.Fatalf("ListEvents returned error: %v", err)
	}

	for _, event := range events {
		if event.Message == "retry_attempt" || strings.Contains(event.Message, "retrying after attempt") {
			t.Fatalf("did not expect retry-related events, got %#v", events)
		}
	}
}

func TestProcessWithRetryUnavailableErrorRetriesAndCompletes(t *testing.T) {
	taskStore := store.NewInMemory()
	seedWorkerTask(t, taskStore, "task-retry-then-complete", domain.TaskQueued, "")

	submitCalls := 0
	agentClient := &fakeAgentClient{
		submitTask: func(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
			submitCalls++
			if submitCalls == 1 {
				return nil, status.Error(codes.Unavailable, "connection refused")
			}

			now := time.Now().UTC().UnixMilli()
			return newScriptedSubmitTaskStream(ctx, []*agentv1.AgentEvent{
				{Type: agentv1.AgentEventType_AGENT_EVENT_TYPE_STARTED, Message: "task started", EmittedAtUnixMs: now},
				{Type: agentv1.AgentEventType_AGENT_EVENT_TYPE_COMPLETED, Message: "task completed", EmittedAtUnixMs: now + 1},
			}), nil
		},
	}

	processor := NewTaskProcessor(taskStore, queue.NewInMemoryQueue(4), agentClient, ProcessorOptions{
		MaxAttempts:  3,
		RetryBackoff: 5 * time.Millisecond,
	})
	processor.processWithRetry(context.Background(), "task-retry-then-complete")

	if submitCalls != 2 {
		t.Fatalf("unexpected submit calls: got %d want 2", submitCalls)
	}

	task, ok := taskStore.Get("task-retry-then-complete")
	if !ok {
		t.Fatal("task not found")
	}
	if task.Status != domain.TaskCompleted {
		t.Fatalf("unexpected task status: got %q want %q", task.Status, domain.TaskCompleted)
	}

	events, err := taskStore.ListEvents("task-retry-then-complete", 0, 30)
	if err != nil {
		t.Fatalf("ListEvents returned error: %v", err)
	}

	foundRetryScheduled := false
	foundRetryAttempt := false
	for _, event := range events {
		if strings.Contains(event.Message, "retrying after attempt 1/3 failed") {
			foundRetryScheduled = true
		}
		if event.Message == "retry_attempt" {
			foundRetryAttempt = true
		}
	}

	if !foundRetryScheduled {
		t.Fatalf("expected retry scheduling info event, got %#v", events)
	}
	if !foundRetryAttempt {
		t.Fatalf("expected retry_attempt marker event, got %#v", events)
	}
}
