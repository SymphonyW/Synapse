package worker

import (
	"context"
	"errors"
	"io"
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
	"google.golang.org/grpc/metadata"
)

// 测试桩客户端，可在测试中自定义提交流与健康检查行为。
type fakeAgentClient struct {
	submitTask func(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error)
	health     func(ctx context.Context) (*agentv1.HealthResponse, error)
}

func (f *fakeAgentClient) SubmitTask(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
	if f.submitTask == nil {
		return nil, errors.New("submitTask not configured")
	}
	return f.submitTask(ctx, task)
}

func (f *fakeAgentClient) Health(ctx context.Context) (*agentv1.HealthResponse, error) {
	if f.health == nil {
		return &agentv1.HealthResponse{Status: "ok", ModelProvider: "test"}, nil
	}
	return f.health(ctx)
}

func (f *fakeAgentClient) MemoryWrite(context.Context, *agentv1.MemoryWriteRequest) (*agentv1.MemoryWriteResponse, error) {
	return &agentv1.MemoryWriteResponse{}, nil
}

func (f *fakeAgentClient) MemoryRecall(context.Context, *agentv1.MemoryRecallRequest) (*agentv1.MemoryRecallResponse, error) {
	return &agentv1.MemoryRecallResponse{}, nil
}

func (f *fakeAgentClient) MemoryDelete(context.Context, *agentv1.MemoryDeleteRequest) (*agentv1.MemoryDeleteResponse, error) {
	return &agentv1.MemoryDeleteResponse{}, nil
}

func (f *fakeAgentClient) MemoryList(context.Context, *agentv1.MemoryListRequest) (*agentv1.MemoryListResponse, error) {
	return &agentv1.MemoryListResponse{}, nil
}

func (f *fakeAgentClient) Close() error {
	return nil
}

// 阻塞流在 Recv 处等待上下文取消，用于模拟运行中任务。
type blockingSubmitTaskStream struct {
	ctx         context.Context
	recvStarted chan struct{}
}

func newBlockingSubmitTaskStream(ctx context.Context) *blockingSubmitTaskStream {
	return &blockingSubmitTaskStream{
		ctx:         ctx,
		recvStarted: make(chan struct{}, 1),
	}
}

func (s *blockingSubmitTaskStream) Recv() (*agentv1.AgentEvent, error) {
	select {
	case s.recvStarted <- struct{}{}:
	default:
	}

	<-s.ctx.Done()
	return nil, s.ctx.Err()
}

func (s *blockingSubmitTaskStream) Header() (metadata.MD, error) {
	return metadata.MD{}, nil
}

func (s *blockingSubmitTaskStream) Trailer() metadata.MD {
	return metadata.MD{}
}

func (s *blockingSubmitTaskStream) CloseSend() error {
	return nil
}

func (s *blockingSubmitTaskStream) Context() context.Context {
	return s.ctx
}

func (s *blockingSubmitTaskStream) SendMsg(any) error {
	return nil
}

func (s *blockingSubmitTaskStream) RecvMsg(any) error {
	<-s.ctx.Done()
	return io.EOF
}

// 验证运行中任务被取消后，最终状态与取消原因保持一致。
func TestProcessWithRetryCancelRunningTaskPreservesReason(t *testing.T) {
	taskStore := store.NewInMemory()
	seedWorkerTask(t, taskStore, "task-running-cancel", domain.TaskQueued, "")

	streamReady := make(chan *blockingSubmitTaskStream, 1)
	agentClient := &fakeAgentClient{
		submitTask: func(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
			stream := newBlockingSubmitTaskStream(ctx)
			streamReady <- stream
			return stream, nil
		},
	}

	processor := NewTaskProcessor(taskStore, queue.NewInMemoryQueue(4), agentClient, ProcessorOptions{ExecutionTimeout: 5 * time.Second})

	done := make(chan struct{})
	go func() {
		defer close(done)
		processor.processWithRetry(context.Background(), "task-running-cancel")
	}()

	var stream *blockingSubmitTaskStream
	select {
	case stream = <-streamReady:
	case <-time.After(2 * time.Second):
		t.Fatal("SubmitTask was not called in time")
	}

	select {
	case <-stream.recvStarted:
	case <-time.After(2 * time.Second):
		t.Fatal("stream Recv was not reached in time")
	}

	waitForTaskStatus(t, taskStore, "task-running-cancel", domain.TaskRunning, 2*time.Second)

	const cancelReason = "canceled by ops-console: emergency stop"
	if _, ok := taskStore.UpdateStatus("task-running-cancel", domain.TaskCanceled, cancelReason); !ok {
		t.Fatal("failed to set canceled status before Cancel")
	}

	if !processor.Cancel("task-running-cancel") {
		t.Fatal("expected processor.Cancel to find active task")
	}

	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("processWithRetry did not finish after cancel")
	}

	task, ok := taskStore.Get("task-running-cancel")
	if !ok {
		t.Fatal("task not found after processing")
	}

	if task.Status != domain.TaskCanceled {
		t.Fatalf("unexpected task status: got %q want %q", task.Status, domain.TaskCanceled)
	}
	if task.Error != cancelReason {
		t.Fatalf("unexpected cancel reason: got %q want %q", task.Error, cancelReason)
	}

	events, err := taskStore.ListEvents("task-running-cancel", 0, 20)
	if err != nil {
		t.Fatalf("ListEvents returned error: %v", err)
	}

	foundCanceledEvent := false
	for _, event := range events {
		if event.Type == "canceled" {
			foundCanceledEvent = true
			break
		}
	}
	if !foundCanceledEvent {
		t.Fatalf("expected canceled event in task events, got %#v", events)
	}
}

// 验证预先取消的任务不会再调用下游提交接口。
func TestProcessWithRetryAlreadyCanceledSkipsSubmit(t *testing.T) {
	taskStore := store.NewInMemory()
	seedWorkerTask(t, taskStore, "task-pre-canceled", domain.TaskCanceled, "canceled by ops-console: batch maintenance")

	submitCalls := 0
	agentClient := &fakeAgentClient{
		submitTask: func(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
			submitCalls++
			return nil, errors.New("should not submit pre-canceled task")
		},
	}

	processor := NewTaskProcessor(taskStore, queue.NewInMemoryQueue(4), agentClient, ProcessorOptions{})
	processor.processWithRetry(context.Background(), "task-pre-canceled")

	if submitCalls != 0 {
		t.Fatalf("unexpected submit calls: got %d want 0", submitCalls)
	}

	task, ok := taskStore.Get("task-pre-canceled")
	if !ok {
		t.Fatal("task not found")
	}
	const expectedReason = "canceled by ops-console: batch maintenance"
	if task.Error != expectedReason {
		t.Fatalf("unexpected cancel reason after finalize: got %q want %q", task.Error, expectedReason)
	}
}

// 轮询等待任务进入目标状态。
func waitForTaskStatus(t *testing.T, taskStore *store.InMemoryStore, taskID string, expected domain.TaskStatus, timeout time.Duration) {
	t.Helper()

	deadline := time.Now().Add(timeout)
	for {
		task, ok := taskStore.Get(taskID)
		if ok && task.Status == expected {
			return
		}

		if time.Now().After(deadline) {
			if !ok {
				t.Fatalf("task %s not found while waiting for status %q", taskID, expected)
			}
			t.Fatalf("timeout waiting for status %q, current status %q", expected, task.Status)
		}

		time.Sleep(20 * time.Millisecond)
	}
}
