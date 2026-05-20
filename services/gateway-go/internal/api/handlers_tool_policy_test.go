package api

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
)

type recordingToolPolicyAgentClient struct {
	noopAgentClient
	appliedPolicy *agentv1.ToolPolicy
}

func (c *recordingToolPolicyAgentClient) GetToolPolicy(context.Context) (*agentv1.GetToolPolicyResponse, error) {
	return &agentv1.GetToolPolicyResponse{
		Policy: &agentv1.ToolPolicy{
			RoleAllow: map[string]*agentv1.StringList{
				"user":  {Items: []string{"calculator"}},
				"admin": {Items: []string{"*"}},
			},
			ApprovalRequired: []string{"calculator"},
			DisabledTools:    []string{},
		},
	}, nil
}

func (c *recordingToolPolicyAgentClient) ApplyToolPolicy(_ context.Context, request *agentv1.ApplyToolPolicyRequest) (*agentv1.ApplyToolPolicyResponse, error) {
	c.appliedPolicy = request.GetPolicy()
	return &agentv1.ApplyToolPolicyResponse{Policy: request.GetPolicy(), Applied: true}, nil
}

func (c *recordingToolPolicyAgentClient) ListTools(context.Context) (*agentv1.ListToolsResponse, error) {
	return &agentv1.ListToolsResponse{
		Items: []*agentv1.ToolDescriptor{
			{Name: "calculator", Description: "math", RiskLevel: "low", ProviderName: "builtin", AllowedRoles: []string{"user", "admin"}},
			{Name: "retrieval", Description: "memory", RiskLevel: "low", ProviderName: "builtin", AllowedRoles: []string{"admin"}},
			{Name: "http_api", Description: "http", RiskLevel: "high", RequiresApproval: true, ProviderName: "builtin", CurrentlyDisabled: true, AllowedRoles: []string{"user", "admin"}},
		},
	}, nil
}

func TestToolPolicyAdminEndpointsPersistAndApply(t *testing.T) {
	taskStore := store.NewInMemory()
	agentClient := &recordingToolPolicyAgentClient{}
	router := NewRouter(NewHandler(taskStore, agentClient, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	getRequest := httptest.NewRequest(http.MethodGet, "/v1/admin/tool-policy", nil)
	attachSessionCookie(t, taskStore, getRequest, "admin", domain.UserRoleAdmin)
	getResponse := httptest.NewRecorder()
	router.ServeHTTP(getResponse, getRequest)

	if getResponse.Code != http.StatusOK {
		t.Fatalf("unexpected get status: got %d want %d", getResponse.Code, http.StatusOK)
	}

	body := bytes.NewBufferString(`{
		"role_allow":{"user":["retrieval"],"admin":["*"]},
		"approval_required":["retrieval"],
		"disabled_tools":["calculator"],
		"description":"lock down calculator"
	}`)
	putRequest := httptest.NewRequest(http.MethodPut, "/v1/admin/tool-policy", body)
	putRequest.Header.Set("Content-Type", "application/json")
	attachSessionCookie(t, taskStore, putRequest, "admin", domain.UserRoleAdmin)
	putResponse := httptest.NewRecorder()
	router.ServeHTTP(putResponse, putRequest)

	if putResponse.Code != http.StatusOK {
		t.Fatalf("unexpected put status: got %d want %d", putResponse.Code, http.StatusOK)
	}
	if agentClient.appliedPolicy == nil {
		t.Fatal("ApplyToolPolicy was not called")
	}
	if agentClient.appliedPolicy.GetVersion() != 1 {
		t.Fatalf("unexpected applied version: got %d want 1", agentClient.appliedPolicy.GetVersion())
	}
	if got := agentClient.appliedPolicy.GetUpdatedBy(); got != "admin" {
		t.Fatalf("unexpected updated_by: got %q want admin", got)
	}

	stored, found, err := taskStore.GetToolPolicy()
	if err != nil {
		t.Fatalf("GetToolPolicy returned error: %v", err)
	}
	if !found {
		t.Fatal("tool policy was not persisted")
	}
	if stored.Description != "lock down calculator" {
		t.Fatalf("unexpected stored description: %q", stored.Description)
	}
}

func TestToolPolicyRejectsUnknownTools(t *testing.T) {
	taskStore := store.NewInMemory()
	agentClient := &recordingToolPolicyAgentClient{}
	router := NewRouter(NewHandler(taskStore, agentClient, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	body := bytes.NewBufferString(`{
		"role_allow":{"user":["future_tool"],"admin":["*"]},
		"approval_required":[],
		"disabled_tools":[]
	}`)
	request := httptest.NewRequest(http.MethodPut, "/v1/admin/tool-policy", body)
	request.Header.Set("Content-Type", "application/json")
	attachSessionCookie(t, taskStore, request, "admin", domain.UserRoleAdmin)
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusBadRequest)
	}
}

func TestToolPolicyAdminEndpointsForbiddenForRegularUser(t *testing.T) {
	taskStore := store.NewInMemory()
	router := NewRouter(NewHandler(taskStore, &recordingToolPolicyAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	request := httptest.NewRequest(http.MethodGet, "/v1/admin/tools", nil)
	attachSessionCookie(t, taskStore, request, "regular-user", domain.UserRoleUser)
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)

	if response.Code != http.StatusForbidden {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusForbidden)
	}
}

func TestListAdminToolsReturnsMetadata(t *testing.T) {
	taskStore := store.NewInMemory()
	router := NewRouter(NewHandler(taskStore, &recordingToolPolicyAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	request := httptest.NewRequest(http.MethodGet, "/v1/admin/tools", nil)
	attachSessionCookie(t, taskStore, request, "admin", domain.UserRoleAdmin)
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusOK)
	}

	var payload struct {
		Items []toolDescriptorResponse `json:"items"`
		Count int                      `json:"count"`
	}
	decodeJSON(t, response, &payload)
	if payload.Count != 3 {
		t.Fatalf("unexpected tool count: got %d want 3", payload.Count)
	}
	if payload.Items[0].Name != "calculator" || payload.Items[0].ProviderName != "builtin" {
		t.Fatalf("unexpected first tool payload: %#v", payload.Items[0])
	}
}

func TestListToolCatalogForAuthenticatedUser(t *testing.T) {
	taskStore := store.NewInMemory()
	router := NewRouter(NewHandler(taskStore, &recordingToolPolicyAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	request := httptest.NewRequest(http.MethodGet, "/v1/tools/catalog", nil)
	attachSessionCookie(t, taskStore, request, "regular-user", domain.UserRoleUser)
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusOK)
	}

	bodyBytes := append([]byte(nil), response.Body.Bytes()...)
	var raw map[string]any
	if err := json.Unmarshal(bodyBytes, &raw); err != nil {
		t.Fatalf("failed to decode raw response: %v", err)
	}
	encodedItems, ok := raw["items"].([]any)
	if !ok {
		t.Fatalf("items should be an array: %#v", raw)
	}
	if _, exists := encodedItems[0].(map[string]any)["allowed_roles"]; exists {
		t.Fatalf("public catalog must not expose allowed_roles: %#v", encodedItems[0])
	}

	var payload struct {
		Items []publicToolDescriptorResponse `json:"items"`
		Count int                            `json:"count"`
	}
	if err := json.Unmarshal(bodyBytes, &payload); err != nil {
		t.Fatalf("failed to decode typed response: %v", err)
	}
	if payload.Count != 3 {
		t.Fatalf("unexpected tool count: got %d want 3", payload.Count)
	}

	byName := map[string]publicToolDescriptorResponse{}
	for _, item := range payload.Items {
		byName[item.Name] = item
	}
	if !byName["calculator"].AllowedForRole || !byName["calculator"].Selectable {
		t.Fatalf("calculator should be selectable for user: %#v", byName["calculator"])
	}
	if byName["retrieval"].AllowedForRole || byName["retrieval"].Selectable {
		t.Fatalf("retrieval should be unavailable for user: %#v", byName["retrieval"])
	}
	if !byName["http_api"].CurrentlyDisabled || byName["http_api"].Selectable {
		t.Fatalf("disabled http_api should not be selectable: %#v", byName["http_api"])
	}
}

func TestListToolCatalogRequiresAuthentication(t *testing.T) {
	taskStore := store.NewInMemory()
	router := NewRouter(NewHandler(taskStore, &recordingToolPolicyAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	request := httptest.NewRequest(http.MethodGet, "/v1/tools/catalog", nil)
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)

	if response.Code != http.StatusUnauthorized {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusUnauthorized)
	}
}
