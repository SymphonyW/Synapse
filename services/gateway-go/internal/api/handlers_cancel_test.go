package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
)

// 用于测试取消逻辑的空客户端，不触发真实 AI 调用。
type noopAgentClient struct{}

func (noopAgentClient) SubmitTask(context.Context, domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
	return nil, nil
}

func (noopAgentClient) Health(context.Context) (*agentv1.HealthResponse, error) {
	return &agentv1.HealthResponse{Status: "ok", ModelProvider: "test"}, nil
}

func (noopAgentClient) MemoryWrite(context.Context, *agentv1.MemoryWriteRequest) (*agentv1.MemoryWriteResponse, error) {
	return &agentv1.MemoryWriteResponse{}, nil
}

func (noopAgentClient) MemoryRecall(context.Context, *agentv1.MemoryRecallRequest) (*agentv1.MemoryRecallResponse, error) {
	return &agentv1.MemoryRecallResponse{}, nil
}

func (noopAgentClient) MemoryDelete(context.Context, *agentv1.MemoryDeleteRequest) (*agentv1.MemoryDeleteResponse, error) {
	return &agentv1.MemoryDeleteResponse{}, nil
}

func (noopAgentClient) MemoryList(context.Context, *agentv1.MemoryListRequest) (*agentv1.MemoryListResponse, error) {
	return &agentv1.MemoryListResponse{}, nil
}

func (noopAgentClient) Close() error {
	return nil
}

// 记录取消调用，便于断言 API 是否向 worker 发出取消信号。
type recordingTaskCanceler struct {
	ids []string
}

func (r *recordingTaskCanceler) Cancel(taskID string) bool {
	r.ids = append(r.ids, taskID)
	return true
}

// 验证首次取消返回 202，并写入审计事件。
func TestCancelTaskAcceptedWithAudit(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-accepted", domain.TaskQueued, "")

	canceler := &recordingTaskCanceler{}
	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), canceler))

	body := []byte(`{"requested_by":"ops-console","reason":"maintenance window"}`)
	request := httptest.NewRequest(http.MethodPost, "/v1/tasks/task-accepted/cancel", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	attachSessionCookie(t, taskStore, request, "ops-console", domain.UserRoleAdmin)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusAccepted {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusAccepted)
	}

	var task domain.Task
	decodeJSON(t, response, &task)

	if task.Status != domain.TaskCanceled {
		t.Fatalf("unexpected task status: got %q want %q", task.Status, domain.TaskCanceled)
	}

	const expectedError = "canceled by ops-console: maintenance window"
	if task.Error != expectedError {
		t.Fatalf("unexpected task error: got %q want %q", task.Error, expectedError)
	}

	if len(canceler.ids) != 1 || canceler.ids[0] != "task-accepted" {
		t.Fatalf("unexpected canceler calls: %#v", canceler.ids)
	}

	events, err := taskStore.ListEvents("task-accepted", 0, 10)
	if err != nil {
		t.Fatalf("ListEvents returned error: %v", err)
	}

	if len(events) != 1 {
		t.Fatalf("unexpected event count: got %d want 1", len(events))
	}

	if events[0].Type != "cancel_requested" {
		t.Fatalf("unexpected event type: got %q want %q", events[0].Type, "cancel_requested")
	}

	const expectedEventMessage = "task cancellation requested by ops-console: maintenance window"
	if events[0].Message != expectedEventMessage {
		t.Fatalf("unexpected event message: got %q want %q", events[0].Message, expectedEventMessage)
	}
}

// 验证幂等取消返回 200，且不重复调用 canceler。
func TestCancelTaskAlreadyCanceledReturnsOK(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-already-canceled", domain.TaskCanceled, "canceled by ops-console: frozen window")

	canceler := &recordingTaskCanceler{}
	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), canceler))

	request := httptest.NewRequest(http.MethodPost, "/v1/tasks/task-already-canceled/cancel", nil)
	attachSessionCookie(t, taskStore, request, "ops-console", domain.UserRoleAdmin)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusOK)
	}

	var task domain.Task
	decodeJSON(t, response, &task)

	if task.Status != domain.TaskCanceled {
		t.Fatalf("unexpected task status: got %q want %q", task.Status, domain.TaskCanceled)
	}

	const expectedError = "canceled by ops-console: frozen window"
	if task.Error != expectedError {
		t.Fatalf("unexpected task error: got %q want %q", task.Error, expectedError)
	}

	if len(canceler.ids) != 0 {
		t.Fatalf("canceler should not be called for already canceled task, got %#v", canceler.ids)
	}
}

// 验证终态任务不能取消，返回 409。
func TestCancelTaskTerminalConflict(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-completed", domain.TaskCompleted, "")

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	request := httptest.NewRequest(http.MethodPost, "/v1/tasks/task-completed/cancel", nil)
	attachSessionCookie(t, taskStore, request, "ops-console", domain.UserRoleAdmin)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusConflict {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusConflict)
	}

	var payload map[string]string
	decodeJSON(t, response, &payload)
	if payload["error"] != "task already in terminal state" {
		t.Fatalf("unexpected error response: %#v", payload)
	}
}

// 验证批量取消的部分成功/失败统计与返回顺序。
func TestBatchCancelTasksPartialFailures(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-queued", domain.TaskQueued, "")
	seedTask(t, taskStore, "task-canceled", domain.TaskCanceled, "canceled by ops-console: existing reason")
	seedTask(t, taskStore, "task-completed", domain.TaskCompleted, "")

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	body := strings.NewReader(`{
		"task_ids": ["task-queued", "task-canceled", "task-completed", "missing", "task-queued", "   "],
		"requested_by": "ops-console",
		"reason": "batch maintenance"
	}`)
	request := httptest.NewRequest(http.MethodPost, "/v1/tasks/cancel", body)
	request.Header.Set("Content-Type", "application/json")
	attachSessionCookie(t, taskStore, request, "ops-console", domain.UserRoleAdmin)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusOK)
	}

	var payload struct {
		Requested       int                  `json:"requested"`
		CanceledCount   int                  `json:"canceled_count"`
		AlreadyCanceled int                  `json:"already_canceled_count"`
		FailedCount     int                  `json:"failed_count"`
		Canceled        []domain.Task        `json:"canceled"`
		Failed          []batchCancelFailure `json:"failed"`
	}
	decodeJSON(t, response, &payload)

	if payload.Requested != 6 {
		t.Fatalf("unexpected requested count: got %d want 6", payload.Requested)
	}
	if payload.CanceledCount != 2 {
		t.Fatalf("unexpected canceled count: got %d want 2", payload.CanceledCount)
	}
	if payload.AlreadyCanceled != 1 {
		t.Fatalf("unexpected already canceled count: got %d want 1", payload.AlreadyCanceled)
	}
	if payload.FailedCount != 2 {
		t.Fatalf("unexpected failed count: got %d want 2", payload.FailedCount)
	}
	if len(payload.Canceled) != 2 {
		t.Fatalf("unexpected canceled payload size: got %d want 2", len(payload.Canceled))
	}
	if payload.Canceled[0].ID != "task-queued" || payload.Canceled[1].ID != "task-canceled" {
		t.Fatalf("unexpected canceled order: got [%s, %s]", payload.Canceled[0].ID, payload.Canceled[1].ID)
	}

	canceledByID := map[string]domain.Task{}
	for _, task := range payload.Canceled {
		canceledByID[task.ID] = task
	}

	if _, ok := canceledByID["task-queued"]; !ok {
		t.Fatalf("task-queued should be canceled: %#v", payload.Canceled)
	}
	if _, ok := canceledByID["task-canceled"]; !ok {
		t.Fatalf("task-canceled should be present as already canceled: %#v", payload.Canceled)
	}

	queuedTask, ok := taskStore.Get("task-queued")
	if !ok {
		t.Fatal("task-queued not found in store")
	}
	if queuedTask.Status != domain.TaskCanceled {
		t.Fatalf("task-queued status not canceled: got %q", queuedTask.Status)
	}
	const expectedQueuedError = "canceled by ops-console: batch maintenance"
	if queuedTask.Error != expectedQueuedError {
		t.Fatalf("task-queued cancel reason mismatch: got %q want %q", queuedTask.Error, expectedQueuedError)
	}
}

// 验证普通用户不能取消其他用户任务。
func TestCancelTaskForbiddenForAnotherUser(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-foreign", domain.TaskQueued, "")

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	request := httptest.NewRequest(http.MethodPost, "/v1/tasks/task-foreign/cancel", nil)
	attachSessionCookie(t, taskStore, request, "regular-user", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusForbidden {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusForbidden)
	}

	var payload map[string]string
	decodeJSON(t, response, &payload)
	if payload["error"] != "forbidden" {
		t.Fatalf("unexpected error response: %#v", payload)
	}

	storedTask, ok := taskStore.Get("task-foreign")
	if !ok {
		t.Fatal("task-foreign not found in store")
	}
	if storedTask.Status != domain.TaskQueued {
		t.Fatalf("task status should remain queued, got %q", storedTask.Status)
	}
}

// 验证未登录请求会被拒绝。
func TestCancelTaskRequiresAuthentication(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-auth-required", domain.TaskQueued, "")

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	request := httptest.NewRequest(http.MethodPost, "/v1/tasks/task-auth-required/cancel", nil)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusUnauthorized {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusUnauthorized)
	}

	var payload map[string]string
	decodeJSON(t, response, &payload)
	if payload["error"] != "unauthorized" {
		t.Fatalf("unexpected error response: %#v", payload)
	}
}

// 验证死信接口仅管理员可访问。
func TestListDeadLettersForbiddenForRegularUser(t *testing.T) {
	taskStore := store.NewInMemory()
	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))

	request := httptest.NewRequest(http.MethodGet, "/v1/dead-letters", nil)
	attachSessionCookie(t, taskStore, request, "regular-user", domain.UserRoleUser)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusForbidden {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusForbidden)
	}

	var payload map[string]string
	decodeJSON(t, response, &payload)
	if payload["error"] != "forbidden" {
		t.Fatalf("unexpected error response: %#v", payload)
	}
}

// 验证 paused 任务可通过审批接口恢复到 queued 并入队。
func TestApproveTaskResumesPausedTask(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-paused", domain.TaskPaused, "task paused: approval required")
	_, _, err := taskStore.UpdateMetadata("task-paused", map[string]string{
		metadataAgentRequiredToolKey: "http_api",
		metadataAgentResumeStepKey:   "2",
	})
	if err != nil {
		t.Fatalf("UpdateMetadata returned error: %v", err)
	}

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))
	body := strings.NewReader(`{"approved_tools":["http_api"],"reason":"reviewed"}`)
	request := httptest.NewRequest(http.MethodPost, "/v1/tasks/task-paused/approve", body)
	request.Header.Set("Content-Type", "application/json")
	attachSessionCookie(t, taskStore, request, "ops-console", domain.UserRoleAdmin)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusAccepted {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusAccepted)
	}

	var task domain.Task
	decodeJSON(t, response, &task)
	if task.Status != domain.TaskQueued {
		t.Fatalf("unexpected task status: got %q want %q", task.Status, domain.TaskQueued)
	}

	storedTask, ok := taskStore.Get("task-paused")
	if !ok {
		t.Fatal("task-paused not found in store")
	}
	if storedTask.Metadata[metadataApprovalGrantedKey] != "true" {
		t.Fatalf("unexpected approval metadata: got %q", storedTask.Metadata[metadataApprovalGrantedKey])
	}
	if storedTask.Metadata[metadataApprovedToolsKey] != "http_api" {
		t.Fatalf("unexpected approved tools metadata: got %q", storedTask.Metadata[metadataApprovedToolsKey])
	}
}

// 验证未显式传 approved_tool_call 时，审批接口会从 paused metadata 还原精确工具调用审批。
func TestApproveTaskWritesApprovedToolCallFromPauseMetadata(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-paused-tool-call", domain.TaskPaused, "task paused: approval required")
	_, _, err := taskStore.UpdateMetadata("task-paused-tool-call", map[string]string{
		metadataAgentRequiredToolKey:      "http_api",
		metadataAgentRequiredToolInputKey: "https://example.com/api",
		metadataAgentRequiredToolRiskKey:  "high",
		metadataAgentRequiredReasonKey:    "external api requires approval",
		metadataAgentResumeStepKey:        "3",
	})
	if err != nil {
		t.Fatalf("UpdateMetadata returned error: %v", err)
	}

	taskQueue := queue.NewInMemoryQueue(8)
	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, taskQueue, &recordingTaskCanceler{}))
	request := httptest.NewRequest(http.MethodPost, "/v1/tasks/task-paused-tool-call/approve", nil)
	attachSessionCookie(t, taskStore, request, "ops-console", domain.UserRoleAdmin)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusAccepted {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusAccepted)
	}

	storedTask, ok := taskStore.Get("task-paused-tool-call")
	if !ok {
		t.Fatal("task-paused-tool-call not found in store")
	}
	if storedTask.Status != domain.TaskQueued {
		t.Fatalf("unexpected task status: got %q want %q", storedTask.Status, domain.TaskQueued)
	}
	if storedTask.Metadata[metadataAgentResumeStepKey] != "3" {
		t.Fatalf("unexpected resume step metadata: got %q", storedTask.Metadata[metadataAgentResumeStepKey])
	}

	var approvedCall approvedToolCallRequest
	if err := json.Unmarshal([]byte(storedTask.Metadata[metadataApprovedToolCallKey]), &approvedCall); err != nil {
		t.Fatalf("approved tool call metadata is not valid JSON: %v", err)
	}
	if approvedCall.ToolName != "http_api" {
		t.Fatalf("unexpected approved tool name: got %q", approvedCall.ToolName)
	}
	if approvedCall.ToolInput != "https://example.com/api" {
		t.Fatalf("unexpected approved tool input: got %q", approvedCall.ToolInput)
	}
	if approvedCall.RiskLevel != "high" {
		t.Fatalf("unexpected approved risk level: got %q", approvedCall.RiskLevel)
	}
	if approvedCall.Reason != "external api requires approval" {
		t.Fatalf("unexpected approved reason: got %q", approvedCall.Reason)
	}
	if approvedCall.ResumeStepIndex != 3 {
		t.Fatalf("unexpected approved resume step: got %d", approvedCall.ResumeStepIndex)
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	queuedTaskID, dequeueErr := taskQueue.Dequeue(ctx)
	if dequeueErr != nil {
		t.Fatalf("Dequeue returned error: %v", dequeueErr)
	}
	if queuedTaskID != "task-paused-tool-call" {
		t.Fatalf("unexpected queued task: got %q want %q", queuedTaskID, "task-paused-tool-call")
	}
}

// 验证非 paused 任务审批请求返回 409。
func TestApproveTaskConflictWhenTaskNotPaused(t *testing.T) {
	taskStore := store.NewInMemory()
	seedTask(t, taskStore, "task-not-paused", domain.TaskQueued, "")

	router := NewRouter(NewHandler(taskStore, noopAgentClient{}, queue.NewInMemoryQueue(8), &recordingTaskCanceler{}))
	request := httptest.NewRequest(http.MethodPost, "/v1/tasks/task-not-paused/approve", nil)
	attachSessionCookie(t, taskStore, request, "ops-console", domain.UserRoleAdmin)
	response := httptest.NewRecorder()

	router.ServeHTTP(response, request)

	if response.Code != http.StatusConflict {
		t.Fatalf("unexpected status: got %d want %d", response.Code, http.StatusConflict)
	}

	var payload map[string]string
	decodeJSON(t, response, &payload)
	if payload["error"] != "task is not paused" {
		t.Fatalf("unexpected error response: %#v", payload)
	}
}

// 构造指定状态的任务测试数据。
func seedTask(t *testing.T, taskStore *store.InMemoryStore, taskID string, status domain.TaskStatus, errMessage string) {
	t.Helper()

	now := time.Now().UTC()
	if err := taskStore.Create(domain.Task{
		ID:        taskID,
		UserID:    "user-test",
		Prompt:    "test prompt",
		Status:    domain.TaskQueued,
		Metadata:  map[string]string{"origin": "test"},
		CreatedAt: now,
		UpdatedAt: now,
	}); err != nil {
		t.Fatalf("Create returned error: %v", err)
	}

	if status != domain.TaskQueued || errMessage != "" {
		if _, ok := taskStore.UpdateStatus(taskID, status, errMessage); !ok {
			t.Fatalf("UpdateStatus returned not found for %s", taskID)
		}
	}
}

// 统一解析 JSON 响应，减少样板代码。
func decodeJSON(t *testing.T, response *httptest.ResponseRecorder, target any) {
	t.Helper()

	if err := json.NewDecoder(response.Body).Decode(target); err != nil {
		t.Fatalf("failed to decode JSON response: %v", err)
	}
}

func attachSessionCookie(
	t *testing.T,
	taskStore *store.InMemoryStore,
	request *http.Request,
	username string,
	role domain.UserRole,
) {
	t.Helper()

	now := time.Now().UTC()
	token := fmt.Sprintf("token-%s-%d", username, now.UnixNano())
	if err := taskStore.CreateSession(domain.AuthSession{
		Token:     token,
		Username:  username,
		Role:      role,
		CreatedAt: now,
		ExpiresAt: now.Add(30 * time.Minute),
	}); err != nil {
		t.Fatalf("CreateSession returned error: %v", err)
	}

	request.AddCookie(&http.Cookie{
		Name:  authSessionCookieName,
		Value: token,
		Path:  "/",
	})
}
