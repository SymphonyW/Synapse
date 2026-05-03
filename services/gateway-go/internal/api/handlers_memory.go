package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
)

const (
	defaultMemoryListLimit = 50
	maxMemoryListLimit     = 200
	memoryAPITimeout       = 3 * time.Second
)

type memoryWriteRequest struct {
	UserID       string  `json:"user_id"`
	Content      string  `json:"content"`
	Summary      string  `json:"summary"`
	SourceTaskID string  `json:"source_task_id"`
	Importance   float64 `json:"importance"`
}

type memoryRecordResponse struct {
	MemoryID     string  `json:"memory_id"`
	UserID       string  `json:"user_id"`
	Content      string  `json:"content"`
	Summary      string  `json:"summary"`
	SourceTaskID string  `json:"source_task_id"`
	Importance   float64 `json:"importance"`
	CreatedAt    int64   `json:"created_at"`
}

type memoryRecallHitResponse struct {
	Record       memoryRecordResponse `json:"record"`
	Score        float64              `json:"score"`
	MatchedTerms []string             `json:"matched_terms"`
}

// WriteMemory 提供手工写入长期记忆的 API，当前仅转发到 AI Engine file backend。
func (h *Handler) WriteMemory(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	var request memoryWriteRequest
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}

	targetUserID := resolveMemoryUserID(session, request.UserID)
	content := strings.TrimSpace(request.Content)
	summary := strings.TrimSpace(request.Summary)
	if content == "" && summary == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "content or summary is required"})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), memoryAPITimeout)
	defer cancel()

	response, err := h.agentClient.MemoryWrite(ctx, &agentv1.MemoryWriteRequest{
		UserId:       targetUserID,
		Content:      content,
		Summary:      summary,
		SourceTaskId: strings.TrimSpace(request.SourceTaskID),
		Importance:   request.Importance,
	})
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "failed to write memory"})
		return
	}

	record := memoryRecordFromProto(response.GetRecord())
	if record.MemoryID == "" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "memory backend unavailable"})
		return
	}

	writeJSON(w, http.StatusCreated, record)
}

// RecallMemory 暴露长期记忆召回 API，便于前端或调试工具检查 Agent 将看到的记忆。
func (h *Handler) RecallMemory(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	query := strings.TrimSpace(r.URL.Query().Get("query"))
	if query == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "query is required"})
		return
	}

	limit, err := parseLimit(r.URL.Query().Get("limit"), defaultMemoryListLimit, maxMemoryListLimit)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid limit"})
		return
	}

	targetUserID := resolveMemoryUserID(session, r.URL.Query().Get("user_id"))
	ctx, cancel := context.WithTimeout(r.Context(), memoryAPITimeout)
	defer cancel()

	response, err := h.agentClient.MemoryRecall(ctx, &agentv1.MemoryRecallRequest{
		UserId: targetUserID,
		Query:  query,
		Limit:  int32(limit),
	})
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "failed to recall memory"})
		return
	}

	hits := make([]memoryRecallHitResponse, 0, len(response.GetHits()))
	for _, hit := range response.GetHits() {
		hits = append(hits, memoryHitFromProto(hit))
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"items": hits,
		"count": len(hits),
	})
}

// ListMemories 返回当前用户的近期长期记忆；管理员可通过 user_id 查询指定用户。
func (h *Handler) ListMemories(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	limit, err := parseLimit(r.URL.Query().Get("limit"), defaultMemoryListLimit, maxMemoryListLimit)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid limit"})
		return
	}

	targetUserID := resolveMemoryUserID(session, r.URL.Query().Get("user_id"))
	ctx, cancel := context.WithTimeout(r.Context(), memoryAPITimeout)
	defer cancel()

	response, err := h.agentClient.MemoryList(ctx, &agentv1.MemoryListRequest{
		UserId: targetUserID,
		Limit:  int32(limit),
	})
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "failed to list memories"})
		return
	}

	items := make([]memoryRecordResponse, 0, len(response.GetItems()))
	for _, record := range response.GetItems() {
		items = append(items, memoryRecordFromProto(record))
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"items": items,
		"count": len(items),
	})
}

// DeleteMemory 删除当前用户的一条长期记忆；管理员可通过 user_id 删除指定用户记忆。
func (h *Handler) DeleteMemory(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireSession(w, r)
	if !ok {
		return
	}

	memoryID := strings.TrimSpace(r.PathValue("memoryID"))
	if memoryID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "memory_id is required"})
		return
	}

	targetUserID := resolveMemoryUserID(session, r.URL.Query().Get("user_id"))
	ctx, cancel := context.WithTimeout(r.Context(), memoryAPITimeout)
	defer cancel()

	response, err := h.agentClient.MemoryDelete(ctx, &agentv1.MemoryDeleteRequest{
		UserId:   targetUserID,
		MemoryId: memoryID,
	})
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "failed to delete memory"})
		return
	}
	if !response.GetDeleted() {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "memory not found"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"deleted":   true,
		"memory_id": memoryID,
	})
}

func resolveMemoryUserID(session domain.AuthSession, requestedUserID string) string {
	if session.Role != domain.UserRoleAdmin {
		return strings.ToLower(strings.TrimSpace(session.Username))
	}

	target := strings.TrimSpace(requestedUserID)
	if target == "" {
		target = session.Username
	}
	return strings.ToLower(target)
}

func memoryRecordFromProto(record *agentv1.MemoryRecord) memoryRecordResponse {
	if record == nil {
		return memoryRecordResponse{}
	}

	return memoryRecordResponse{
		MemoryID:     record.GetMemoryId(),
		UserID:       record.GetUserId(),
		Content:      record.GetContent(),
		Summary:      record.GetSummary(),
		SourceTaskID: record.GetSourceTaskId(),
		Importance:   record.GetImportance(),
		CreatedAt:    record.GetCreatedAt(),
	}
}

func memoryHitFromProto(hit *agentv1.MemoryRecallHit) memoryRecallHitResponse {
	if hit == nil {
		return memoryRecallHitResponse{}
	}

	return memoryRecallHitResponse{
		Record:       memoryRecordFromProto(hit.GetRecord()),
		Score:        hit.GetScore(),
		MatchedTerms: append([]string{}, hit.GetMatchedTerms()...),
	}
}
