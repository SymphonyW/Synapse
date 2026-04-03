package api

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
)

// 验证管理员无法通过 user_id 冒充其他用户创建任务。
func TestCreateTaskIgnoresRequestedUserIDForAdmin(t *testing.T) {
	taskStore := store.NewInMemory()
	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	requestBody := []byte(`{"user_id":"victim-user","prompt":"hello from ops"}`)
	request := httptest.NewRequest(http.MethodPost, "/v1/tasks", bytes.NewReader(requestBody))
	request.Header.Set("Content-Type", "application/json")
	attachSessionCookie(t, taskStore, request, "admin", domain.UserRoleAdmin)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusCreated {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusCreated)
	}

	var task domain.Task
	decodeJSON(t, response, &task)

	if task.UserID != "admin" {
		t.Fatalf("task user_id should be locked to session user: got %q want %q", task.UserID, "admin")
	}

	storedTask, ok := taskStore.Get(task.ID)
	if !ok {
		t.Fatal("created task not found in store")
	}
	if storedTask.UserID != "admin" {
		t.Fatalf("stored task user_id should be locked to session user: got %q want %q", storedTask.UserID, "admin")
	}
}
