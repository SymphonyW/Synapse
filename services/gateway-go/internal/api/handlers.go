package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/synapse/synapse/services/gateway-go/internal/agent"
	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
)

// Handler 持有全部 HTTP 端点实现，并把持久化、排队、取消能力委托给注入的抽象层。
type Handler struct {
	store        store.TaskStore
	agentClient  agent.Client
	taskQueue    queue.TaskQueue
	taskCanceler TaskCanceler
}

// TaskCanceler 允许 API 层通知 worker 终止正在运行的任务。
type TaskCanceler interface {
	Cancel(taskID string) bool
}

type createTaskRequest struct {
	UserID   string            `json:"user_id"`
	Prompt   string            `json:"prompt"`
	Metadata map[string]string `json:"metadata"`
}

type cancelTaskRequest struct {
	RequestedBy string `json:"requested_by"`
	Reason      string `json:"reason"`
}

type approveTaskRequest struct {
	RequestedBy   string   `json:"requested_by"`
	Reason        string   `json:"reason"`
	ApprovedTools []string `json:"approved_tools"`
}

type batchCancelTasksRequest struct {
	TaskIDs     []string `json:"task_ids"`
	RequestedBy string   `json:"requested_by"`
	Reason      string   `json:"reason"`
}

type batchCancelFailure struct {
	TaskID string `json:"task_id"`
	Error  string `json:"error"`
}

type deleteConversationResponse struct {
	ConversationID string   `json:"conversation_id"`
	DeletedCount   int      `json:"deleted_count"`
	DeletedTaskIDs []string `json:"deleted_task_ids"`
}

var errTaskTerminalState = errors.New("task already in terminal state")
var errTaskPermissionDenied = errors.New("permission denied")

const (
	// 死信列表接口限制最大返回数量，避免响应体失控。
	defaultDeadLetterLimit = 100
	maxDeadLetterLimit     = 500
	// 任务列表默认参数偏向控制台场景（近期任务优先）。
	defaultTaskListLimit = 50
	maxTaskListLimit     = 500
	// 会话上下文仅保留最近若干轮，避免 prompt 无界增长。
	conversationContextTurnLimit = 8
	conversationEventLoadLimit   = 8000
	conversationTurnMaxChars     = 1200

	metadataConversationIDKey         = "conversation_id"
	metadataUserMessageKey            = "user_message"
	metadataClientViewKey             = "client_view"
	metadataModelPromptKey            = "model_prompt"
	metadataModelMessagesKey          = "model_messages_json"
	metadataAuthUserRoleKey           = "auth_user_role"
	metadataAuthUsernameKey           = "auth_username"
	metadataAgentEnabledKey           = "agent_enabled"
	metadataMemoryWriteKey            = "memory_write_enabled"
	metadataApprovalGrantedKey        = "approval_granted"
	metadataApprovedToolsKey          = "approved_tools"
	metadataAgentResumeStepKey        = "agent_resume_step_index"
	metadataAgentRequiredToolKey      = "agent_required_tool"
	metadataAgentResumeRequestedByKey = "agent_resume_requested_by"
)

type conversationTurn struct {
	User      string
	Assistant string
}

func NewHandler(taskStore store.TaskStore, agentClient agent.Client, taskQueue queue.TaskQueue, taskCanceler TaskCanceler) *Handler {
	return &Handler{
		store:        taskStore,
		agentClient:  agentClient,
		taskQueue:    taskQueue,
		taskCanceler: taskCanceler,
	}
}

// Healthz 通过 gRPC 检查 AI 引擎可达性，并返回简化健康信息。
func (h *Handler) Healthz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()

	health, err := h.agentClient.Health(ctx)
	if err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"status": "degraded",
			"error":  err.Error(),
		})
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"status":         "ok",
		"ai_engine":      health.Status,
		"model_provider": health.ModelProvider,
	})
}

// CreateTask 校验请求并落库为 queued，再把任务 ID 入队等待异步执行。
func (h *Handler) CreateTask(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	var request createTaskRequest

	// 明确拒绝未知字段，避免客户端静默发送错误参数。
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}

	userID := strings.TrimSpace(session.Username)
	userMessage := strings.TrimSpace(request.Prompt)
	if userMessage == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "prompt is required"})
		return
	}

	if userID == "" {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
		return
	}

	// 新任务从 queued 状态进入生命周期，后续由 worker 切换为 running。
	now := time.Now().UTC()
	task := domain.Task{
		ID:        uuid.NewString(),
		UserID:    userID,
		Prompt:    userMessage,
		Status:    domain.TaskQueued,
		Metadata:  request.Metadata,
		CreatedAt: now,
		UpdatedAt: now,
	}

	if task.Metadata == nil {
		task.Metadata = map[string]string{}
	}

	// model_prompt 由网关负责构建，忽略客户端透传值，避免上下文注入污染。
	delete(task.Metadata, metadataModelPromptKey)
	delete(task.Metadata, metadataModelMessagesKey)
	delete(task.Metadata, metadataAuthUserRoleKey)
	delete(task.Metadata, metadataAuthUsernameKey)

	// 权限元数据由网关注入，禁止客户端伪造。
	task.Metadata[metadataAuthUserRoleKey] = string(session.Role)
	task.Metadata[metadataAuthUsernameKey] = userID

	if _, exists := task.Metadata[metadataAgentEnabledKey]; !exists {
		task.Metadata[metadataAgentEnabledKey] = "true"
	}
	if _, exists := task.Metadata[metadataMemoryWriteKey]; !exists {
		task.Metadata[metadataMemoryWriteKey] = "true"
	}

	conversationID := strings.TrimSpace(task.Metadata[metadataConversationIDKey])
	isConversationTask := conversationID != "" || strings.EqualFold(strings.TrimSpace(task.Metadata[metadataClientViewKey]), "chat")
	if isConversationTask {
		if conversationID == "" {
			conversationID = uuid.NewString()
		}

		task.Metadata[metadataConversationIDKey] = conversationID
		task.Metadata[metadataUserMessageKey] = userMessage

		historyTurns, err := h.loadConversationTurns(userID, conversationID, conversationContextTurnLimit)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to build conversation context"})
			return
		}

		if len(historyTurns) > 0 {
			task.Metadata[metadataModelPromptKey] = buildConversationModelPrompt(historyTurns, userMessage)
		}

		modelMessagesJSON, err := buildConversationModelMessagesJSON(historyTurns, userMessage)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to encode conversation messages"})
			return
		}
		task.Metadata[metadataModelMessagesKey] = modelMessagesJSON
	}

	// 先落库后入队，保证 worker 消费前任务已经可查询。
	if err := h.store.Create(task); err != nil {
		if errors.Is(err, store.ErrTaskAlreadyExists) {
			writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to create task"})
		return
	}

	// 若落库后入队失败，则把任务标记为 failed 并追加事件，避免前端长期停留在 queued。
	if err := h.taskQueue.Enqueue(r.Context(), task.ID); err != nil {
		h.store.UpdateStatus(task.ID, domain.TaskFailed, "failed to enqueue task")
		_, _ = h.store.AppendEvent(task.ID, domain.TaskEvent{
			Type:            "failed",
			Message:         "failed to enqueue task",
			EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
		})
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to enqueue task"})
		return
	}

	writeJSON(w, http.StatusCreated, task)
}

// GetTask 按 ID 返回单个任务。
func (h *Handler) GetTask(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	taskID := r.PathValue("taskID")
	task, ok := h.store.Get(taskID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "task not found"})
		return
	}

	if !canAccessTask(session, task) {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "forbidden"})
		return
	}

	writeJSON(w, http.StatusOK, task)
}

// ListTasks 返回近期任务，可按状态过滤。
func (h *Handler) ListTasks(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	limit, err := parseLimit(r.URL.Query().Get("limit"), defaultTaskListLimit, maxTaskListLimit)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid limit"})
		return
	}

	status := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("status")))
	// 状态值做严格校验，避免拼写错误导致“看似成功但结果为空”。
	if status != "" && !isAllowedTaskStatus(status) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid status"})
		return
	}

	storeLimit := limit
	if session.Role != domain.UserRoleAdmin {
		storeLimit = maxTaskListLimit
	}

	tasks, err := h.store.ListTasks(storeLimit, status)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to list tasks"})
		return
	}

	if session.Role != domain.UserRoleAdmin {
		filtered := make([]domain.Task, 0, len(tasks))
		for _, task := range tasks {
			if canAccessTask(session, task) {
				filtered = append(filtered, task)
			}
		}

		if len(filtered) > limit {
			filtered = filtered[:limit]
		}

		tasks = filtered
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"items": tasks,
		"count": len(tasks),
	})
}

// DeleteConversation 删除当前用户某会话下的全部任务（含事件与死信记录）。
func (h *Handler) DeleteConversation(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	conversationID := strings.TrimSpace(r.PathValue("conversationID"))
	if conversationID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "conversation_id is required"})
		return
	}

	deletedTaskIDs, err := h.store.DeleteTasksByConversation(session.Username, conversationID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to delete conversation"})
		return
	}

	if len(deletedTaskIDs) == 0 {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "conversation not found"})
		return
	}

	if h.taskCanceler != nil {
		for _, taskID := range deletedTaskIDs {
			h.taskCanceler.Cancel(taskID)
		}
	}

	writeJSON(w, http.StatusOK, deleteConversationResponse{
		ConversationID: conversationID,
		DeletedCount:   len(deletedTaskIDs),
		DeletedTaskIDs: deletedTaskIDs,
	})
}

// ReplayTask 将非 running 任务重置为 queued，并重新入队执行。
func (h *Handler) ReplayTask(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	taskID := r.PathValue("taskID")
	task, ok := h.store.Get(taskID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "task not found"})
		return
	}

	if !canAccessTask(session, task) {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "forbidden"})
		return
	}

	if task.Status == domain.TaskRunning {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "task is currently running"})
		return
	}

	// 重放前清空历史错误与死信记录，让任务以干净状态重新开始。
	updatedTask, ok := h.store.UpdateStatus(taskID, domain.TaskQueued, "")
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "task not found"})
		return
	}

	_ = h.store.ClearDeadLetter(taskID)
	_, _ = h.store.AppendEvent(taskID, domain.TaskEvent{
		Type:            "replay_requested",
		Message:         "task replay requested",
		EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
	})

	if err := h.taskQueue.Enqueue(r.Context(), taskID); err != nil {
		h.store.UpdateStatus(taskID, domain.TaskFailed, "failed to enqueue replay task")
		_, _ = h.store.AppendEvent(taskID, domain.TaskEvent{
			Type:            "failed",
			Message:         "failed to enqueue replay task",
			EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
		})
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to enqueue replay task"})
		return
	}

	writeJSON(w, http.StatusAccepted, updatedTask)
}

// ApproveTask 对 paused 任务授予审批并重新入队，从暂停点恢复执行。
func (h *Handler) ApproveTask(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	taskID := r.PathValue("taskID")
	request, err := parseApproveTaskRequest(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}
	request.RequestedBy = session.Username

	task, exists := h.store.Get(taskID)
	if !exists {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "task not found"})
		return
	}

	if !canAccessTask(session, task) {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "forbidden"})
		return
	}

	if task.Status != domain.TaskPaused {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "task is not paused"})
		return
	}

	approvedTools := normalizeApprovedTools(request.ApprovedTools)
	if len(approvedTools) == 0 {
		requiredTool := strings.TrimSpace(task.Metadata[metadataAgentRequiredToolKey])
		if requiredTool != "" {
			approvedTools = append(approvedTools, requiredTool)
		}
	}

	metadataUpdates := map[string]string{
		metadataApprovalGrantedKey:        "true",
		metadataAuthUserRoleKey:           string(session.Role),
		metadataAuthUsernameKey:           session.Username,
		metadataAgentResumeRequestedByKey: session.Username,
	}
	if len(approvedTools) > 0 {
		metadataUpdates[metadataApprovedToolsKey] = strings.Join(approvedTools, ",")
	}

	if _, ok, updateErr := h.store.UpdateMetadata(taskID, metadataUpdates); updateErr != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to persist approval metadata"})
		return
	} else if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "task not found"})
		return
	}

	updatedTask, ok := h.store.UpdateStatus(taskID, domain.TaskQueued, "")
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "task not found"})
		return
	}

	approvalMessage := "approval granted"
	if request.RequestedBy != "" {
		approvalMessage = "approval granted by " + request.RequestedBy
	}
	if request.Reason != "" {
		approvalMessage += ": " + request.Reason
	}

	_, _ = h.store.AppendEvent(taskID, domain.TaskEvent{
		Type:            "approval_granted",
		Message:         approvalMessage,
		EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
	})
	_, _ = h.store.AppendEvent(taskID, domain.TaskEvent{
		Type:            "resume_requested",
		Message:         "task resume requested",
		EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
	})

	if err := h.taskQueue.Enqueue(r.Context(), taskID); err != nil {
		h.store.UpdateStatus(taskID, domain.TaskFailed, "failed to enqueue approved task")
		_, _ = h.store.AppendEvent(taskID, domain.TaskEvent{
			Type:            "failed",
			Message:         "failed to enqueue approved task",
			EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
		})
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to enqueue task"})
		return
	}

	writeJSON(w, http.StatusAccepted, updatedTask)
}

// BatchCancelTasks 逐个处理取消请求，并返回部分成功结果供调用方重试失败项。
func (h *Handler) BatchCancelTasks(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	request, err := parseBatchCancelTasksRequest(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}
	request.RequestedBy = session.Username

	if len(request.TaskIDs) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "task_ids is required"})
		return
	}

	statusByTaskID := map[string]domain.Task{}
	// 保持首见顺序，确保前端渲染稳定且可预测。
	orderedCanceledTaskIDs := make([]string, 0, len(request.TaskIDs))
	failed := make([]batchCancelFailure, 0)
	alreadyCanceledCount := 0

	for _, taskID := range request.TaskIDs {
		taskID = strings.TrimSpace(taskID)
		if taskID == "" {
			// 空白任务 ID 直接忽略，提升批量接口对前端输入的容错性。
			continue
		}

		// 请求内去重，避免对同一任务重复执行取消。
		if _, exists := statusByTaskID[taskID]; exists {
			continue
		}

		orderedCanceledTaskIDs = append(orderedCanceledTaskIDs, taskID)

		task, alreadyCanceled, cancelErr := h.cancelTaskInternal(taskID, cancelTaskRequest{
			RequestedBy: request.RequestedBy,
			Reason:      request.Reason,
		}, session)
		if cancelErr != nil {
			failed = append(failed, batchCancelFailure{TaskID: taskID, Error: cancelErr.Error()})
			continue
		}

		if alreadyCanceled {
			alreadyCanceledCount++
		}

		statusByTaskID[taskID] = task
	}

	// 按原始请求顺序构造 canceled 列表。
	canceled := make([]domain.Task, 0, len(statusByTaskID))
	for _, taskID := range orderedCanceledTaskIDs {
		task, exists := statusByTaskID[taskID]
		if !exists {
			continue
		}
		canceled = append(canceled, task)
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"requested":              len(request.TaskIDs),
		"canceled_count":         len(canceled),
		"already_canceled_count": alreadyCanceledCount,
		"failed_count":           len(failed),
		"canceled":               canceled,
		"failed":                 failed,
	})
}

// CancelTask 处理单任务取消：首次取消返回 202，幂等取消返回 200。
func (h *Handler) CancelTask(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	taskID := r.PathValue("taskID")
	request, err := parseCancelTaskRequest(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}
	request.RequestedBy = session.Username

	updatedTask, alreadyCanceled, cancelErr := h.cancelTaskInternal(taskID, request, session)
	if cancelErr != nil {
		switch {
		case errors.Is(cancelErr, store.ErrTaskNotFound):
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "task not found"})
		case errors.Is(cancelErr, errTaskPermissionDenied):
			writeJSON(w, http.StatusForbidden, map[string]string{"error": "forbidden"})
		case errors.Is(cancelErr, errTaskTerminalState):
			writeJSON(w, http.StatusConflict, map[string]string{"error": "task already in terminal state"})
		default:
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to cancel task"})
		}
		return
	}

	if alreadyCanceled {
		writeJSON(w, http.StatusOK, updatedTask)
		return
	}

	writeJSON(w, http.StatusAccepted, updatedTask)
}

// ListDeadLetters 返回已耗尽重试、需要人工介入的任务。
func (h *Handler) ListDeadLetters(w http.ResponseWriter, r *http.Request) {
	_, ok := h.requireAdminSession(w, r)
	if !ok {
		return
	}

	limit, err := parseLimit(r.URL.Query().Get("limit"), defaultDeadLetterLimit, maxDeadLetterLimit)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid limit"})
		return
	}

	entries, err := h.store.ListDeadLetters(limit)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to list dead letters"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"items": entries,
		"count": len(entries),
	})
}

// StreamTaskEvents 为单任务提供增量 SSE 事件流。
// 支持通过 last_event_id 续传；当任务进入终态且无新事件时发送 terminal 并关闭连接。
func (h *Handler) StreamTaskEvents(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	taskID := r.PathValue("taskID")
	task, ok := h.store.Get(taskID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "task not found"})
		return
	}

	if !canAccessTask(session, task) {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "forbidden"})
		return
	}

	lastEventID, err := parseLastEventID(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid last_event_id"})
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")

	flusher, ok := w.(http.Flusher)
	if !ok {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "streaming is not supported by this server"})
		return
	}

	if err := writeSSE(w, "info", map[string]any{"message": "stream_opened", "task_id": taskID, "last_event_id": lastEventID}); err != nil {
		return
	}
	flusher.Flush()

	// 通过短周期轮询持久化事件而非内存状态，实现 SSE 层与 worker 运行态解耦，支持断线重连续传。
	// 轮询间隔适当降低，避免前端看到“成批刷新”的迟滞感。
	ticker := time.NewTicker(120 * time.Millisecond)
	defer ticker.Stop()

	for {
		events, err := h.store.ListEvents(taskID, lastEventID, 200)
		if errors.Is(err, store.ErrTaskNotFound) {
			_ = writeSSE(w, "failed", map[string]any{"message": "task not found", "task_id": taskID})
			flusher.Flush()
			return
		}
		if err != nil {
			_ = writeSSE(w, "failed", map[string]any{"message": "failed to list task events", "task_id": taskID})
			flusher.Flush()
			return
		}

		for _, event := range events {
			payload := map[string]any{
				"event_id":           event.ID,
				"type":               event.Type,
				"message":            event.Message,
				"token":              event.Token,
				"trace_id":           event.TraceID,
				"emitted_at_unix_ms": event.EmittedAtUnixMS,
			}

			if err := writeSSE(w, event.Type, payload); err != nil {
				return
			}
			// 记录已下发的最新事件 ID，避免下一轮轮询重复发送。
			lastEventID = event.ID
			flusher.Flush()
		}

		task, ok := h.store.Get(taskID)
		if !ok {
			return
		}

		if (task.Status == domain.TaskCompleted || task.Status == domain.TaskFailed || task.Status == domain.TaskCanceled) && len(events) == 0 {
			if err := writeSSE(w, "terminal", map[string]any{"task_id": taskID, "status": task.Status}); err != nil {
				return
			}
			flusher.Flush()
			return
		}

		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
		}
	}
}

// loadConversationTurns 从持久化任务与事件中还原最近已完成会话轮次。
func (h *Handler) loadConversationTurns(userID string, conversationID string, limit int) ([]conversationTurn, error) {
	tasks, err := h.store.ListTasksByConversation(userID, conversationID, limit)
	if err != nil {
		return nil, err
	}

	turns := make([]conversationTurn, 0, len(tasks))
	for _, task := range tasks {
		// 仅使用已成功完成的轮次构建上下文，避免把失败/限流错误文本注入后续请求。
		if task.Status != domain.TaskCompleted {
			continue
		}

		userMessage := strings.TrimSpace(task.Metadata[metadataUserMessageKey])
		if userMessage == "" {
			userMessage = strings.TrimSpace(task.Prompt)
		}

		assistantMessage, err := h.resolveAssistantMessage(task)
		if err != nil {
			return nil, err
		}
		assistantMessage = strings.TrimSpace(assistantMessage)
		if !isEligibleConversationAssistantMessage(assistantMessage) {
			continue
		}

		if userMessage == "" || assistantMessage == "" {
			continue
		}

		turns = append(turns, conversationTurn{
			User:      truncateForConversationContext(userMessage, conversationTurnMaxChars),
			Assistant: truncateForConversationContext(assistantMessage, conversationTurnMaxChars),
		})
	}

	if limit > 0 && len(turns) > limit {
		turns = turns[len(turns)-limit:]
	}

	return turns, nil
}

// resolveAssistantMessage 优先拼接 token 事件；若无 token 则回退到失败/取消原因。
func (h *Handler) resolveAssistantMessage(task domain.Task) (string, error) {
	events, err := h.store.ListEvents(task.ID, 0, conversationEventLoadLimit)
	if err != nil && !errors.Is(err, store.ErrTaskNotFound) {
		return "", err
	}

	var tokenBuilder strings.Builder
	fallbackMessage := ""
	for _, event := range events {
		switch event.Type {
		case "token":
			tokenBuilder.WriteString(event.Token)
		case "failed", "canceled":
			message := strings.TrimSpace(event.Message)
			if message != "" {
				fallbackMessage = message
			}
		}
	}

	if tokens := strings.TrimSpace(tokenBuilder.String()); tokens != "" {
		return tokens, nil
	}

	if fallbackMessage != "" {
		return fallbackMessage, nil
	}

	if task.Status == domain.TaskFailed || task.Status == domain.TaskCanceled {
		return strings.TrimSpace(task.Error), nil
	}

	return "", nil
}

// buildConversationModelPrompt 将历史轮次与当前用户消息组装为模型输入。
func buildConversationModelPrompt(history []conversationTurn, currentUserMessage string) string {
	var builder strings.Builder
	builder.WriteString("You are Synapse assistant. Continue this multi-turn conversation and keep responses practical.\n\n")
	builder.WriteString("Conversation history:\n")

	for _, turn := range history {
		builder.WriteString("User: ")
		builder.WriteString(turn.User)
		builder.WriteString("\nAssistant: ")
		builder.WriteString(turn.Assistant)
		builder.WriteString("\n\n")
	}

	builder.WriteString("User: ")
	builder.WriteString(truncateForConversationContext(strings.TrimSpace(currentUserMessage), conversationTurnMaxChars))
	builder.WriteString("\nAssistant:")

	return builder.String()
}

func buildConversationModelMessagesJSON(history []conversationTurn, currentUserMessage string) (string, error) {
	type openAIMessage struct {
		Role    string `json:"role"`
		Content string `json:"content"`
	}

	messages := make([]openAIMessage, 0, len(history)*2+2)
	messages = append(messages, openAIMessage{
		Role:    "system",
		Content: "You are Synapse assistant. Continue this multi-turn conversation and keep responses practical.",
	})

	for _, turn := range history {
		messages = append(messages,
			openAIMessage{Role: "user", Content: turn.User},
			openAIMessage{Role: "assistant", Content: turn.Assistant},
		)
	}

	messages = append(messages, openAIMessage{
		Role:    "user",
		Content: truncateForConversationContext(strings.TrimSpace(currentUserMessage), conversationTurnMaxChars),
	})

	encoded, err := json.Marshal(messages)
	if err != nil {
		return "", err
	}

	return string(encoded), nil
}

func truncateForConversationContext(message string, maxChars int) string {
	trimmed := strings.TrimSpace(message)
	if trimmed == "" || maxChars <= 0 {
		return ""
	}

	runes := []rune(trimmed)
	if len(runes) <= maxChars {
		return trimmed
	}

	return strings.TrimSpace(string(runes[:maxChars]))
}

func isEligibleConversationAssistantMessage(message string) bool {
	normalized := strings.TrimSpace(message)
	if normalized == "" {
		return false
	}

	lower := strings.ToLower(normalized)
	blockedMarkers := []string{
		"task execution summary (fallback)",
		"model service is temporarily unavailable",
		"模型服务暂时不可用",
		"rpc error",
		"context deadline exceeded",
	}

	for _, marker := range blockedMarkers {
		if strings.Contains(lower, marker) {
			return false
		}
	}

	return true
}

// parseLastEventID 解析并校验 SSE 续传游标。
func parseLastEventID(r *http.Request) (int64, error) {
	value := strings.TrimSpace(r.URL.Query().Get("last_event_id"))
	if value == "" {
		return 0, nil
	}

	parsed, err := strconv.ParseInt(value, 10, 64)
	if err != nil || parsed < 0 {
		return 0, errors.New("invalid last_event_id")
	}

	return parsed, nil
}

// parseLimit 解析并裁剪 limit 参数到允许范围。
func parseLimit(raw string, defaultValue int, maxValue int) (int, error) {
	value := strings.TrimSpace(raw)
	if value == "" {
		return defaultValue, nil
	}

	parsed, err := strconv.Atoi(value)
	if err != nil || parsed <= 0 {
		return 0, errors.New("invalid limit")
	}

	if parsed > maxValue {
		parsed = maxValue
	}

	return parsed, nil
}

// parseCancelTaskRequest 支持可选 body；空 body 表示使用默认取消信息。
func parseCancelTaskRequest(r *http.Request) (cancelTaskRequest, error) {
	if r.Body == nil {
		return cancelTaskRequest{}, nil
	}

	data, err := io.ReadAll(io.LimitReader(r.Body, 8*1024))
	if err != nil {
		return cancelTaskRequest{}, err
	}

	if strings.TrimSpace(string(data)) == "" {
		return cancelTaskRequest{}, nil
	}

	var request cancelTaskRequest
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		return cancelTaskRequest{}, err
	}

	request.RequestedBy = strings.TrimSpace(request.RequestedBy)
	request.Reason = strings.TrimSpace(request.Reason)
	return request, nil
}

// parseApproveTaskRequest 支持可选 body；空 body 表示使用默认审批参数。
func parseApproveTaskRequest(r *http.Request) (approveTaskRequest, error) {
	if r.Body == nil {
		return approveTaskRequest{}, nil
	}

	data, err := io.ReadAll(io.LimitReader(r.Body, 16*1024))
	if err != nil {
		return approveTaskRequest{}, err
	}

	if strings.TrimSpace(string(data)) == "" {
		return approveTaskRequest{}, nil
	}

	var request approveTaskRequest
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		return approveTaskRequest{}, err
	}

	request.RequestedBy = strings.TrimSpace(request.RequestedBy)
	request.Reason = strings.TrimSpace(request.Reason)
	request.ApprovedTools = normalizeApprovedTools(request.ApprovedTools)
	return request, nil
}

func normalizeApprovedTools(rawTools []string) []string {
	if len(rawTools) == 0 {
		return []string{}
	}

	seen := map[string]struct{}{}
	normalized := make([]string, 0, len(rawTools))
	for _, tool := range rawTools {
		value := strings.ToLower(strings.TrimSpace(tool))
		if value == "" {
			continue
		}
		if _, exists := seen[value]; exists {
			continue
		}
		seen[value] = struct{}{}
		normalized = append(normalized, value)
	}

	return normalized
}

// parseBatchCancelTasksRequest 要求 JSON body，并对 task_ids 做去空白处理。
func parseBatchCancelTasksRequest(r *http.Request) (batchCancelTasksRequest, error) {
	if r.Body == nil {
		return batchCancelTasksRequest{}, errors.New("missing body")
	}

	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	var request batchCancelTasksRequest
	if err := decoder.Decode(&request); err != nil {
		return batchCancelTasksRequest{}, err
	}

	request.RequestedBy = strings.TrimSpace(request.RequestedBy)
	request.Reason = strings.TrimSpace(request.Reason)
	for i := range request.TaskIDs {
		request.TaskIDs[i] = strings.TrimSpace(request.TaskIDs[i])
	}

	return request, nil
}

// cancelTaskInternal 统一封装单取消与批量取消共享的业务规则。
func (h *Handler) cancelTaskInternal(taskID string, request cancelTaskRequest, session domain.AuthSession) (domain.Task, bool, error) {
	task, ok := h.store.Get(taskID)
	if !ok {
		return domain.Task{}, false, store.ErrTaskNotFound
	}

	if !canAccessTask(session, task) {
		return domain.Task{}, false, errTaskPermissionDenied
	}

	if task.Status == domain.TaskCanceled {
		// 幂等路径：已经取消则直接返回当前状态。
		return task, true, nil
	}

	// completed/failed 视为不可变终态，不允许再执行取消。
	if task.Status == domain.TaskCompleted || task.Status == domain.TaskFailed {
		return domain.Task{}, false, errTaskTerminalState
	}

	cancelMessage := "canceled by user"
	if request.RequestedBy != "" {
		cancelMessage = "canceled by " + request.RequestedBy
	}
	if request.Reason != "" {
		cancelMessage += ": " + request.Reason
	}

	updatedTask, ok := h.store.UpdateStatus(taskID, domain.TaskCanceled, cancelMessage)
	if !ok {
		return domain.Task{}, false, store.ErrTaskNotFound
	}

	eventMessage := "task cancellation requested"
	if request.RequestedBy != "" {
		eventMessage = "task cancellation requested by " + request.RequestedBy
	}
	if request.Reason != "" {
		eventMessage += ": " + request.Reason
	}

	// 一旦进入取消流程，死信记录不再适用，直接清理。
	_ = h.store.ClearDeadLetter(taskID)
	_, _ = h.store.AppendEvent(taskID, domain.TaskEvent{
		Type:            "cancel_requested",
		Message:         eventMessage,
		EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
	})

	if h.taskCanceler != nil {
		// 尽力通知：任务可能在信号到达前已结束。
		h.taskCanceler.Cancel(taskID)
	}

	return updatedTask, false, nil
}

func (h *Handler) requireSession(w http.ResponseWriter, r *http.Request) (domain.AuthSession, bool) {
	return h.readSessionFromRequest(w, r)
}

func (h *Handler) requireAdminSession(w http.ResponseWriter, r *http.Request) (domain.AuthSession, bool) {
	session, ok := h.readSessionFromRequest(w, r)
	if !ok {
		return domain.AuthSession{}, false
	}

	if session.Role != domain.UserRoleAdmin {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "forbidden"})
		return domain.AuthSession{}, false
	}

	return session, true
}

func canAccessTask(session domain.AuthSession, task domain.Task) bool {
	if session.Role == domain.UserRoleAdmin {
		return true
	}

	return strings.EqualFold(strings.TrimSpace(session.Username), strings.TrimSpace(task.UserID))
}

// isAllowedTaskStatus 集中维护状态过滤参数的合法值。
func isAllowedTaskStatus(status string) bool {
	switch status {
	case string(domain.TaskQueued), string(domain.TaskRunning), string(domain.TaskPaused), string(domain.TaskCompleted), string(domain.TaskFailed), string(domain.TaskCanceled):
		return true
	default:
		return false
	}
}

// writeSSE 写入单条 SSE 帧（事件名 + JSON 数据）。
func writeSSE(w http.ResponseWriter, eventName string, payload any) error {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return err
	}

	_, err = fmt.Fprintf(w, "event: %s\ndata: %s\n\n", eventName, encoded)
	return err
}

// writeJSON 统一 JSON 响应编码与状态码写入。
func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}
