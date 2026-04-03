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

type batchCancelTasksRequest struct {
	TaskIDs     []string `json:"task_ids"`
	RequestedBy string   `json:"requested_by"`
	Reason      string   `json:"reason"`
}

type batchCancelFailure struct {
	TaskID string `json:"task_id"`
	Error  string `json:"error"`
}

var errTaskTerminalState = errors.New("task already in terminal state")

const (
	// 死信列表接口限制最大返回数量，避免响应体失控。
	defaultDeadLetterLimit = 100
	maxDeadLetterLimit     = 500
	// 任务列表默认参数偏向控制台场景（近期任务优先）。
	defaultTaskListLimit = 50
	maxTaskListLimit     = 500
)

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
	var request createTaskRequest

	// 明确拒绝未知字段，避免客户端静默发送错误参数。
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}

	if strings.TrimSpace(request.UserID) == "" || strings.TrimSpace(request.Prompt) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "user_id and prompt are required"})
		return
	}

	// 新任务从 queued 状态进入生命周期，后续由 worker 切换为 running。
	now := time.Now().UTC()
	task := domain.Task{
		ID:        uuid.NewString(),
		UserID:    request.UserID,
		Prompt:    request.Prompt,
		Status:    domain.TaskQueued,
		Metadata:  request.Metadata,
		CreatedAt: now,
		UpdatedAt: now,
	}

	if task.Metadata == nil {
		task.Metadata = map[string]string{}
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
	taskID := r.PathValue("taskID")
	task, ok := h.store.Get(taskID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "task not found"})
		return
	}

	writeJSON(w, http.StatusOK, task)
}

// ListTasks 返回近期任务，可按状态过滤。
func (h *Handler) ListTasks(w http.ResponseWriter, r *http.Request) {
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

	tasks, err := h.store.ListTasks(limit, status)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to list tasks"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"items": tasks,
		"count": len(tasks),
	})
}

// ReplayTask 将非 running 任务重置为 queued，并重新入队执行。
func (h *Handler) ReplayTask(w http.ResponseWriter, r *http.Request) {
	taskID := r.PathValue("taskID")
	task, ok := h.store.Get(taskID)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "task not found"})
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

// BatchCancelTasks 逐个处理取消请求，并返回部分成功结果供调用方重试失败项。
func (h *Handler) BatchCancelTasks(w http.ResponseWriter, r *http.Request) {
	request, err := parseBatchCancelTasksRequest(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}

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
		})
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
	taskID := r.PathValue("taskID")
	request, err := parseCancelTaskRequest(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}

	updatedTask, alreadyCanceled, cancelErr := h.cancelTaskInternal(taskID, request)
	if cancelErr != nil {
		switch {
		case errors.Is(cancelErr, store.ErrTaskNotFound):
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "task not found"})
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
	taskID := r.PathValue("taskID")
	if _, ok := h.store.Get(taskID); !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "task not found"})
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

	// 通过轮询持久化事件而非内存状态，实现 SSE 层与 worker 运行态解耦，支持断线重连续传。
	ticker := time.NewTicker(300 * time.Millisecond)
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
func (h *Handler) cancelTaskInternal(taskID string, request cancelTaskRequest) (domain.Task, bool, error) {
	task, ok := h.store.Get(taskID)
	if !ok {
		return domain.Task{}, false, store.ErrTaskNotFound
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

// isAllowedTaskStatus 集中维护状态过滤参数的合法值。
func isAllowedTaskStatus(status string) bool {
	switch status {
	case string(domain.TaskQueued), string(domain.TaskRunning), string(domain.TaskCompleted), string(domain.TaskFailed), string(domain.TaskCanceled):
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
