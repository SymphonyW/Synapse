package api

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
)

// 验证删除会话会移除该用户会话下全部任务与关联记录，并保留其他会话数据。
func TestDeleteConversationRemovesScopedTasks(t *testing.T) {
	taskStore := store.NewInMemory()
	now := time.Now().UTC()

	mustCreateTaskWithConversation(t, taskStore, domain.Task{
		ID:        "task-conv-1",
		UserID:    "alice",
		Prompt:    "hello 1",
		Status:    domain.TaskRunning,
		Metadata:  map[string]string{"conversation_id": "conv-a", "client_view": "chat"},
		CreatedAt: now,
		UpdatedAt: now,
	})
	mustCreateTaskWithConversation(t, taskStore, domain.Task{
		ID:        "task-conv-2",
		UserID:    "alice",
		Prompt:    "hello 2",
		Status:    domain.TaskCompleted,
		Metadata:  map[string]string{"conversation_id": "conv-a", "client_view": "chat"},
		CreatedAt: now,
		UpdatedAt: now,
	})
	mustCreateTaskWithConversation(t, taskStore, domain.Task{
		ID:        "task-other-conv",
		UserID:    "alice",
		Prompt:    "other",
		Status:    domain.TaskQueued,
		Metadata:  map[string]string{"conversation_id": "conv-b", "client_view": "chat"},
		CreatedAt: now,
		UpdatedAt: now,
	})
	mustCreateTaskWithConversation(t, taskStore, domain.Task{
		ID:        "task-other-user",
		UserID:    "bob",
		Prompt:    "foreign",
		Status:    domain.TaskQueued,
		Metadata:  map[string]string{"conversation_id": "conv-a", "client_view": "chat"},
		CreatedAt: now,
		UpdatedAt: now,
	})

	_, err := taskStore.AppendEvent("task-conv-1", domain.TaskEvent{Type: "token", Token: "x"})
	if err != nil {
		t.Fatalf("AppendEvent returned error: %v", err)
	}
	if err := taskStore.MarkDeadLetter("task-conv-2", "boom", 3); err != nil {
		t.Fatalf("MarkDeadLetter returned error: %v", err)
	}

	canceler := &recordingTaskCanceler{}
	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), canceler))

	request := httptest.NewRequest(http.MethodDelete, "/v1/conversations/conv-a", nil)
	attachSessionCookie(t, taskStore, request, "alice", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusOK)
	}

	var payload deleteConversationResponse
	decodeJSON(t, response, &payload)
	if payload.ConversationID != "conv-a" {
		t.Fatalf("unexpected conversation id: got %q want %q", payload.ConversationID, "conv-a")
	}
	if payload.DeletedCount != 2 {
		t.Fatalf("unexpected deleted count: got %d want %d", payload.DeletedCount, 2)
	}

	if _, ok := taskStore.Get("task-conv-1"); ok {
		t.Fatal("task-conv-1 should be deleted")
	}
	if _, ok := taskStore.Get("task-conv-2"); ok {
		t.Fatal("task-conv-2 should be deleted")
	}

	if _, ok := taskStore.Get("task-other-conv"); !ok {
		t.Fatal("task-other-conv should remain")
	}
	if _, ok := taskStore.Get("task-other-user"); !ok {
		t.Fatal("task-other-user should remain")
	}

	if len(canceler.ids) != 2 {
		t.Fatalf("unexpected canceler calls: %#v", canceler.ids)
	}
}

// 验证删除会话支持历史兼容：无 conversation_id 时按 task.id 匹配。
func TestDeleteConversationSupportsLegacyTaskIDFallback(t *testing.T) {
	taskStore := store.NewInMemory()
	now := time.Now().UTC()

	mustCreateTaskWithConversation(t, taskStore, domain.Task{
		ID:        "legacy-task-id",
		UserID:    "alice",
		Prompt:    "legacy",
		Status:    domain.TaskQueued,
		Metadata:  map[string]string{"client_view": "chat"},
		CreatedAt: now,
		UpdatedAt: now,
	})

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	request := httptest.NewRequest(http.MethodDelete, "/v1/conversations/legacy-task-id", nil)
	attachSessionCookie(t, taskStore, request, "alice", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusOK)
	}

	if _, ok := taskStore.Get("legacy-task-id"); ok {
		t.Fatal("legacy-task-id should be deleted")
	}
}

// 验证删除不存在会话返回 404。
func TestDeleteConversationNotFound(t *testing.T) {
	taskStore := store.NewInMemory()
	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	request := httptest.NewRequest(http.MethodDelete, "/v1/conversations/not-exists", nil)
	attachSessionCookie(t, taskStore, request, "alice", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusNotFound {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusNotFound)
	}
}

func mustCreateTaskWithConversation(t *testing.T, taskStore *store.InMemoryStore, task domain.Task) {
	t.Helper()

	if err := taskStore.Create(task); err != nil {
		t.Fatalf("Create returned error: %v", err)
	}
}
