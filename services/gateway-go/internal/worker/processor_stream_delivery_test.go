package worker

import (
	"context"
	"io"
	"sync"
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
	"google.golang.org/grpc/metadata"
)

type recordingQueue struct {
	mu       sync.Mutex
	acked    []queue.Delivery
	reclaims []queue.Delivery
}

func (q *recordingQueue) Enqueue(context.Context, string) error { return nil }
func (q *recordingQueue) Claim(context.Context, string) (queue.Delivery, error) {
	return queue.Delivery{}, queue.ErrNoDelivery
}
func (q *recordingQueue) Ack(_ context.Context, delivery queue.Delivery) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.acked = append(q.acked, delivery)
	return nil
}
func (q *recordingQueue) Reclaim(context.Context, string, time.Duration, int) ([]queue.Delivery, error) {
	q.mu.Lock()
	defer q.mu.Unlock()
	return append([]queue.Delivery(nil), q.reclaims...), nil
}
func (q *recordingQueue) Stats(context.Context) (queue.Stats, error) {
	return queue.Stats{Backend: "recording"}, nil
}
func (q *recordingQueue) Close() error { return nil }

func (q *recordingQueue) ackCount() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	return len(q.acked)
}

func TestHandleDeliveryAcksTerminalAndPausedTasksWithoutSubmit(t *testing.T) {
	for _, status := range []domain.TaskStatus{domain.TaskCompleted, domain.TaskPaused, domain.TaskCanceled, domain.TaskFailed} {
		t.Run(string(status), func(t *testing.T) {
			taskStore := store.NewInMemory()
			taskID := "task-" + string(status)
			seedWorkerTask(t, taskStore, taskID, status, "")

			submitCalls := 0
			agentClient := &fakeAgentClient{
				submitTask: func(context.Context, domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
					submitCalls++
					return nil, io.EOF
				},
			}
			taskQueue := &recordingQueue{}
			processor := NewTaskProcessor(taskStore, taskQueue, agentClient, ProcessorOptions{ConsumerName: "worker-a"})

			processor.handleDelivery(context.Background(), queue.Delivery{MessageID: "1-0", TaskID: taskID, Consumer: "worker-b", DeliveryCount: 2})

			if submitCalls != 0 {
				t.Fatalf("terminal/paused task should not submit, got %d calls", submitCalls)
			}
			if taskQueue.ackCount() != 1 {
				t.Fatalf("expected duplicate delivery to be acked")
			}
		})
	}
}

func TestHandleDeliveryPoisonMessageDeadLettersAndAcks(t *testing.T) {
	taskStore := store.NewInMemory()
	seedWorkerTask(t, taskStore, "task-poison", domain.TaskQueued, "")

	submitCalls := 0
	agentClient := &fakeAgentClient{
		submitTask: func(context.Context, domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
			submitCalls++
			return nil, io.EOF
		},
	}
	taskQueue := &recordingQueue{}
	processor := NewTaskProcessor(taskStore, taskQueue, agentClient, ProcessorOptions{ConsumerName: "worker-a", MaxAttempts: 3})

	processor.handleDelivery(context.Background(), queue.Delivery{MessageID: "2-0", TaskID: "task-poison", Consumer: "worker-b", DeliveryCount: 4})

	if submitCalls != 0 {
		t.Fatalf("poison delivery should not submit, got %d calls", submitCalls)
	}
	task, ok := taskStore.Get("task-poison")
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
	if len(deadLetters) != 1 || deadLetters[0].Attempts != 4 {
		t.Fatalf("unexpected dead letter state: %#v", deadLetters)
	}
	if taskQueue.ackCount() != 1 {
		t.Fatalf("expected poison delivery to be acked")
	}
}

func TestConcurrentWorkersOnlyOneAcquiresExecutionLease(t *testing.T) {
	taskStore := store.NewInMemory()
	seedWorkerTask(t, taskStore, "task-lease-race", domain.TaskQueued, "")

	release := make(chan struct{})
	submitCalls := make(chan string, 2)
	agentClient := &fakeAgentClient{
		submitTask: func(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
			submitCalls <- task.ID
			return &gatedCompleteStream{ctx: ctx, release: release}, nil
		},
	}

	queueA := &recordingQueue{}
	queueB := &recordingQueue{}
	workerA := NewTaskProcessor(taskStore, queueA, agentClient, ProcessorOptions{
		ConsumerName:      "worker-a",
		VisibilityTimeout: time.Minute,
	})
	workerB := NewTaskProcessor(taskStore, queueB, agentClient, ProcessorOptions{
		ConsumerName:      "worker-b",
		VisibilityTimeout: time.Minute,
	})

	doneA := make(chan struct{})
	go func() {
		defer close(doneA)
		workerA.handleDelivery(context.Background(), queue.Delivery{MessageID: "1-0", TaskID: "task-lease-race", Consumer: "worker-a", DeliveryCount: 1})
	}()

	select {
	case <-submitCalls:
	case <-time.After(2 * time.Second):
		t.Fatal("worker A did not start processing")
	}

	workerB.handleDelivery(context.Background(), queue.Delivery{MessageID: "2-0", TaskID: "task-lease-race", Consumer: "worker-b", DeliveryCount: 1})
	if queueB.ackCount() != 1 {
		t.Fatalf("worker B should ack duplicate delivery")
	}

	select {
	case <-submitCalls:
		t.Fatal("worker B should not submit task while worker A owns lease")
	default:
	}

	close(release)
	select {
	case <-doneA:
	case <-time.After(2 * time.Second):
		t.Fatal("worker A did not finish")
	}
	if queueA.ackCount() != 1 {
		t.Fatalf("worker A should ack original delivery")
	}
}

type gatedCompleteStream struct {
	ctx       context.Context
	release   <-chan struct{}
	recvCount int
}

func (s *gatedCompleteStream) Recv() (*agentv1.AgentEvent, error) {
	s.recvCount++
	now := time.Now().UTC().UnixMilli()
	switch s.recvCount {
	case 1:
		return &agentv1.AgentEvent{Type: agentv1.AgentEventType_AGENT_EVENT_TYPE_STARTED, Message: "started", EmittedAtUnixMs: now}, nil
	case 2:
		select {
		case <-s.ctx.Done():
			return nil, s.ctx.Err()
		case <-s.release:
			return &agentv1.AgentEvent{Type: agentv1.AgentEventType_AGENT_EVENT_TYPE_COMPLETED, Message: "completed", EmittedAtUnixMs: now + 1}, nil
		}
	default:
		return nil, io.EOF
	}
}

func (s *gatedCompleteStream) Header() (metadata.MD, error) { return metadata.MD{}, nil }
func (s *gatedCompleteStream) Trailer() metadata.MD         { return metadata.MD{} }
func (s *gatedCompleteStream) CloseSend() error             { return nil }
func (s *gatedCompleteStream) Context() context.Context     { return s.ctx }
func (s *gatedCompleteStream) SendMsg(any) error            { return nil }
func (s *gatedCompleteStream) RecvMsg(any) error            { return io.EOF }
