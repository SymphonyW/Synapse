package store

import (
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
)

func TestInMemoryStorePersistsAgentEventV2Payload(t *testing.T) {
	taskStore := NewInMemory()
	now := time.Now().UTC()
	if err := taskStore.Create(domain.Task{
		ID:        "task-event-v2",
		UserID:    "user-1",
		Prompt:    "test",
		Status:    domain.TaskQueued,
		CreatedAt: now,
		UpdatedAt: now,
	}); err != nil {
		t.Fatalf("Create returned error: %v", err)
	}

	persisted, err := taskStore.AppendEvent("task-event-v2", domain.TaskEvent{
		Type:          "info",
		SchemaVersion: "synapse.agent.event.v2",
		EventName:     "tool_finished",
		Payload: map[string]any{
			"tool":           "calculator",
			"output_preview": "calculator result: 72",
			"nested": map[string]any{
				"ok": true,
			},
		},
	})
	if err != nil {
		t.Fatalf("AppendEvent returned error: %v", err)
	}
	persisted.Payload["tool"] = "mutated"

	events, err := taskStore.ListEvents("task-event-v2", 0, 10)
	if err != nil {
		t.Fatalf("ListEvents returned error: %v", err)
	}
	if len(events) != 1 {
		t.Fatalf("unexpected event count: got %d want 1", len(events))
	}

	event := events[0]
	if event.SchemaVersion != "synapse.agent.event.v2" {
		t.Fatalf("unexpected schema version: got %q", event.SchemaVersion)
	}
	if event.EventName != "tool_finished" {
		t.Fatalf("unexpected event name: got %q", event.EventName)
	}
	if event.Payload["tool"] != "calculator" {
		t.Fatalf("payload was not persisted defensively: %#v", event.Payload)
	}
}
