package api

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
)

func TestReplayTaskCreatesChildTaskWithFormalRelationship(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-origin", domain.TaskFailed, "boom")

	taskQueue := queue.NewInMemoryQueue(8)
	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, taskQueue, &recordingTaskCanceler{}))

	request := httptest.NewRequest(http.MethodPost, "/v1/tasks/task-origin/replay", nil)
	attachSessionCookie(t, taskStore, request, "user-test", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusAccepted {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusAccepted)
	}

	var replay domain.Task
	decodeJSON(t, response, &replay)
	if replay.ID == "task-origin" {
		t.Fatal("replay should create a new child task")
	}
	if replay.ReplayOfTaskID != "task-origin" {
		t.Fatalf("unexpected replay parent: got %q want %q", replay.ReplayOfTaskID, "task-origin")
	}
	if replay.Status != domain.TaskQueued {
		t.Fatalf("unexpected replay status: got %q want %q", replay.Status, domain.TaskQueued)
	}

	origin, ok := taskStore.Get("task-origin")
	if !ok {
		t.Fatal("origin task not found")
	}
	if origin.Status != domain.TaskFailed {
		t.Fatalf("origin status should remain unchanged, got %q", origin.Status)
	}

	replays, err := taskStore.ListReplays("task-origin", 10)
	if err != nil {
		t.Fatalf("ListReplays returned error: %v", err)
	}
	if len(replays) != 1 || replays[0].ID != replay.ID {
		t.Fatalf("unexpected replay list: %#v", replays)
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	queuedTaskID, err := taskQueue.Dequeue(ctx)
	if err != nil {
		t.Fatalf("Dequeue returned error: %v", err)
	}
	if queuedTaskID != replay.ID {
		t.Fatalf("unexpected queued task: got %q want %q", queuedTaskID, replay.ID)
	}
}

func TestListTaskReplaysReturnsChildren(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-origin", domain.TaskCompleted, "")
	createReplayChild(t, taskStore, "task-replay-1", "task-origin", "user-test")
	createReplayChild(t, taskStore, "task-replay-2", "task-origin", "user-test")

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))
	request := httptest.NewRequest(http.MethodGet, "/v1/tasks/task-origin/replays", nil)
	attachSessionCookie(t, taskStore, request, "user-test", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusOK)
	}

	var payload struct {
		Items []domain.Task `json:"items"`
		Count int           `json:"count"`
	}
	decodeJSON(t, response, &payload)

	if payload.Count != 2 || len(payload.Items) != 2 {
		t.Fatalf("unexpected replay payload: %#v", payload)
	}
}

func TestListTaskReplaysForbiddenForAnotherUser(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-origin", domain.TaskCompleted, "")

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))
	request := httptest.NewRequest(http.MethodGet, "/v1/tasks/task-origin/replays", nil)
	attachSessionCookie(t, taskStore, request, "other-user", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusForbidden {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusForbidden)
	}
}

func TestCompareReplayReturnsBothTaskSnapshotsAndEvents(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-origin", domain.TaskCompleted, "")
	createReplayChild(t, taskStore, "task-replay", "task-origin", "user-test")
	appendInfoEvent(t, taskStore, "task-origin", `{"agent_event":"plan","payload":{"step_count":1,"steps":["origin"]}}`)
	appendInfoEvent(t, taskStore, "task-replay", `{"agent_event":"plan","payload":{"step_count":2,"steps":["replay-a","replay-b"]}}`)

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))
	request := httptest.NewRequest(http.MethodGet, "/v1/tasks/task-origin/compare/task-replay", nil)
	attachSessionCookie(t, taskStore, request, "user-test", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusOK)
	}

	var payload struct {
		BaseTask    domain.Task        `json:"base_task"`
		OtherTask   domain.Task        `json:"other_task"`
		BaseEvents  []domain.TaskEvent `json:"base_events"`
		OtherEvents []domain.TaskEvent `json:"other_events"`
	}
	decodeJSON(t, response, &payload)

	if payload.BaseTask.ID != "task-origin" || payload.OtherTask.ID != "task-replay" {
		t.Fatalf("unexpected compare tasks: %#v", payload)
	}
	if len(payload.BaseEvents) != 1 || len(payload.OtherEvents) != 1 {
		t.Fatalf("unexpected compare events: %#v", payload)
	}
}

func TestCompareReplayForbiddenForAnotherUser(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-origin", domain.TaskCompleted, "")
	createReplayChild(t, taskStore, "task-replay", "task-origin", "user-test")

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))
	request := httptest.NewRequest(http.MethodGet, "/v1/tasks/task-origin/compare/task-replay", nil)
	attachSessionCookie(t, taskStore, request, "other-user", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusForbidden {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusForbidden)
	}
}

func TestCompareReplayRejectsUnrelatedTask(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-origin", domain.TaskCompleted, "")
	seedTask(t, taskStore, "task-unrelated", domain.TaskCompleted, "")

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))
	request := httptest.NewRequest(http.MethodGet, "/v1/tasks/task-origin/compare/task-unrelated", nil)
	attachSessionCookie(t, taskStore, request, "user-test", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusBadRequest)
	}
}

func createReplayChild(t *testing.T, taskStore *store.InMemoryStore, taskID string, replayOf string, userID string) {
	t.Helper()

	now := time.Now().UTC()
	if err := taskStore.Create(domain.Task{
		ID:             taskID,
		UserID:         userID,
		Prompt:         "test prompt",
		Status:         domain.TaskQueued,
		ReplayOfTaskID: replayOf,
		Metadata:       map[string]string{"origin": "test"},
		CreatedAt:      now,
		UpdatedAt:      now,
	}); err != nil {
		t.Fatalf("Create replay child returned error: %v", err)
	}
}

func appendInfoEvent(t *testing.T, taskStore *store.InMemoryStore, taskID string, message string) {
	t.Helper()

	if _, err := taskStore.AppendEvent(taskID, domain.TaskEvent{
		Type:            "info",
		Message:         message,
		EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
	}); err != nil {
		t.Fatalf("AppendEvent returned error: %v", err)
	}
}
