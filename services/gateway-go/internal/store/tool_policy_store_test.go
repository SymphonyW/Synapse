package store

import (
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
)

func TestInMemoryToolPolicyRoundTrip(t *testing.T) {
	policy := domain.ToolPolicy{
		RoleAllow: map[string][]string{
			"user":  {"retrieval"},
			"admin": {"*"},
		},
		ApprovalRequired: []string{"retrieval"},
		DisabledTools:    []string{"calculator"},
		Version:          1,
		UpdatedAt:        time.Unix(100, 0).UTC(),
		UpdatedBy:        "admin",
		Description:      "managed",
	}

	store := NewInMemory()
	saved, err := store.UpsertToolPolicy(policy)
	if err != nil {
		t.Fatalf("UpsertToolPolicy returned error: %v", err)
	}
	if saved.Version != 1 {
		t.Fatalf("unexpected saved version: got %d want 1", saved.Version)
	}

	got, found, err := store.GetToolPolicy()
	if err != nil {
		t.Fatalf("GetToolPolicy returned error: %v", err)
	}
	if !found {
		t.Fatal("expected tool policy to exist")
	}
	if got.Description != "managed" || got.RoleAllow["user"][0] != "retrieval" {
		t.Fatalf("unexpected policy snapshot: %#v", got)
	}
}
