package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/agentinfo"
	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
)

func TestStreamTaskEventsIncludesAgentEventV2Fields(t *testing.T) {
	taskStore := store.NewInMemory()
	now := time.Now().UTC()
	if err := taskStore.Create(domain.Task{
		ID:        "task-event-stream-v2",
		UserID:    "alice",
		Prompt:    "calculate",
		Status:    domain.TaskCompleted,
		CreatedAt: now,
		UpdatedAt: now,
	}); err != nil {
		t.Fatalf("Create returned error: %v", err)
	}
	if _, err := taskStore.AppendEvent("task-event-stream-v2", domain.TaskEvent{
		Type:          "info",
		Message:       `{"agent_event":"tool_finished","payload":{"tool":"calculator"}}`,
		SchemaVersion: agentinfo.SchemaV2,
		EventName:     "tool_finished",
		Payload: map[string]any{
			"tool":           "calculator",
			"output_preview": "calculator result: 72",
		},
		EmittedAtUnixMS: now.UnixMilli(),
	}); err != nil {
		t.Fatalf("AppendEvent returned error: %v", err)
	}

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))
	request := httptest.NewRequest(http.MethodGet, "/v1/tasks/task-event-stream-v2/events", nil)
	attachSessionCookie(t, taskStore, request, "alice", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusOK)
	}
	body := response.Body.String()
	for _, expected := range []string{
		`"schema_version":"synapse.agent.event.v2"`,
		`"event_name":"tool_finished"`,
		`"payload":`,
		`"tool":"calculator"`,
		`"output_preview":"calculator result: 72"`,
	} {
		if !strings.Contains(body, expected) {
			t.Fatalf("SSE body missing %s: %s", expected, body)
		}
	}
}
