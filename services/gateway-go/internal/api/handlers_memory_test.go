package api

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
)

type recordingMemoryAgentClient struct {
	noopAgentClient
	writeRequest  *agentv1.MemoryWriteRequest
	recallRequest *agentv1.MemoryRecallRequest
	listRequest   *agentv1.MemoryListRequest
	deleteRequest *agentv1.MemoryDeleteRequest
}

func (c *recordingMemoryAgentClient) MemoryWrite(ctx context.Context, request *agentv1.MemoryWriteRequest) (*agentv1.MemoryWriteResponse, error) {
	_ = ctx
	c.writeRequest = request
	return &agentv1.MemoryWriteResponse{
		Record: &agentv1.MemoryRecord{
			MemoryId:     "mem-1",
			UserId:       request.GetUserId(),
			Content:      request.GetContent(),
			Summary:      request.GetSummary(),
			SourceTaskId: request.GetSourceTaskId(),
			Importance:   request.GetImportance(),
			CreatedAt:    123,
		},
	}, nil
}

func (c *recordingMemoryAgentClient) MemoryRecall(ctx context.Context, request *agentv1.MemoryRecallRequest) (*agentv1.MemoryRecallResponse, error) {
	_ = ctx
	c.recallRequest = request
	return &agentv1.MemoryRecallResponse{
		Hits: []*agentv1.MemoryRecallHit{
			{
				Record: &agentv1.MemoryRecord{
					MemoryId: "mem-1",
					UserId:   request.GetUserId(),
					Content:  "gateway retries are bounded",
					Summary:  "bounded retries",
				},
				Score:        1.2,
				MatchedTerms: []string{"gateway"},
			},
		},
	}, nil
}

func (c *recordingMemoryAgentClient) MemoryList(ctx context.Context, request *agentv1.MemoryListRequest) (*agentv1.MemoryListResponse, error) {
	_ = ctx
	c.listRequest = request
	return &agentv1.MemoryListResponse{
		Items: []*agentv1.MemoryRecord{
			{
				MemoryId:   "mem-1",
				UserId:     request.GetUserId(),
				Content:    "remember gateway retry policy",
				Summary:    "retry policy",
				Importance: 0.8,
				CreatedAt:  123,
			},
		},
	}, nil
}

func (c *recordingMemoryAgentClient) MemoryDelete(ctx context.Context, request *agentv1.MemoryDeleteRequest) (*agentv1.MemoryDeleteResponse, error) {
	_ = ctx
	c.deleteRequest = request
	return &agentv1.MemoryDeleteResponse{Deleted: true}, nil
}

// 验证普通用户无法通过 user_id 写入或查询他人的长期记忆。
func TestMemoryWriteLocksUserToSession(t *testing.T) {
	taskStore := store.NewInMemory()
	agentClient := &recordingMemoryAgentClient{}
	router := NewRouter(NewHandler(taskStore, agentClient, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	body := bytes.NewReader([]byte(`{
		"user_id":"victim",
		"content":"remember gateway retries",
		"summary":"gateway retry policy",
		"source_task_id":"task-1",
		"importance":0.9
	}`))
	request := httptest.NewRequest(http.MethodPost, "/v1/memories", body)
	request.Header.Set("Content-Type", "application/json")
	attachSessionCookie(t, taskStore, request, "regular-user", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusCreated {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusCreated)
	}
	if agentClient.writeRequest == nil {
		t.Fatal("MemoryWrite was not called")
	}
	if agentClient.writeRequest.GetUserId() != "regular-user" {
		t.Fatalf("memory user should be locked to session: got %q", agentClient.writeRequest.GetUserId())
	}

	var payload memoryRecordResponse
	decodeJSON(t, response, &payload)
	if payload.UserID != "regular-user" || payload.MemoryID != "mem-1" {
		t.Fatalf("unexpected memory response: %#v", payload)
	}
}

// 验证管理员可以通过 API 管理指定用户的长期记忆。
func TestMemoryAdminListRecallAndDelete(t *testing.T) {
	taskStore := store.NewInMemory()
	agentClient := &recordingMemoryAgentClient{}
	router := NewRouter(NewHandler(taskStore, agentClient, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	listRequest := httptest.NewRequest(http.MethodGet, "/v1/memories?user_id=target&limit=10", nil)
	attachSessionCookie(t, taskStore, listRequest, "admin", domain.UserRoleAdmin)
	listResponse := httptest.NewRecorder()
	router.ServeHTTP(listResponse, listRequest)

	if listResponse.Code != http.StatusOK {
		t.Fatalf("unexpected list status: got %d want %d", listResponse.Code, http.StatusOK)
	}
	if agentClient.listRequest.GetUserId() != "target" {
		t.Fatalf("unexpected list user: %q", agentClient.listRequest.GetUserId())
	}

	recallRequest := httptest.NewRequest(http.MethodGet, "/v1/memories/recall?user_id=target&query=gateway&limit=5", nil)
	attachSessionCookie(t, taskStore, recallRequest, "admin", domain.UserRoleAdmin)
	recallResponse := httptest.NewRecorder()
	router.ServeHTTP(recallResponse, recallRequest)

	if recallResponse.Code != http.StatusOK {
		t.Fatalf("unexpected recall status: got %d want %d", recallResponse.Code, http.StatusOK)
	}
	if agentClient.recallRequest.GetQuery() != "gateway" {
		t.Fatalf("unexpected recall query: %q", agentClient.recallRequest.GetQuery())
	}

	deleteRequest := httptest.NewRequest(http.MethodDelete, "/v1/memories/mem-1?user_id=target", nil)
	attachSessionCookie(t, taskStore, deleteRequest, "admin", domain.UserRoleAdmin)
	deleteResponse := httptest.NewRecorder()
	router.ServeHTTP(deleteResponse, deleteRequest)

	if deleteResponse.Code != http.StatusOK {
		t.Fatalf("unexpected delete status: got %d want %d", deleteResponse.Code, http.StatusOK)
	}
	if agentClient.deleteRequest.GetMemoryId() != "mem-1" {
		t.Fatalf("unexpected deleted memory id: %q", agentClient.deleteRequest.GetMemoryId())
	}
}
