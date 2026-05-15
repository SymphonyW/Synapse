package api

import (
	"context"
	"encoding/json"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/agent"
	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
)

const toolPolicyAPITimeout = 3 * time.Second

var toolPolicyNamePattern = regexp.MustCompile(`^[a-z0-9_]+$`)

type toolPolicyWriteRequest struct {
	RoleAllow        map[string][]string `json:"role_allow"`
	ApprovalRequired []string            `json:"approval_required"`
	DisabledTools    []string            `json:"disabled_tools"`
	Description      string              `json:"description"`
}

type toolPolicyEnvelope struct {
	Source  string            `json:"source"`
	Applied bool              `json:"applied"`
	Policy  domain.ToolPolicy `json:"policy"`
}

type toolDescriptorResponse struct {
	Name              string   `json:"name"`
	Description       string   `json:"description"`
	RiskLevel         string   `json:"risk_level"`
	RequiresApproval  bool     `json:"requires_approval"`
	ProviderName      string   `json:"provider_name"`
	CurrentlyDisabled bool     `json:"currently_disabled"`
	AllowedRoles      []string `json:"allowed_roles"`
}

func (h *Handler) GetToolPolicy(w http.ResponseWriter, r *http.Request) {
	if _, ok := h.requireAdminSession(w, r); !ok {
		return
	}

	if policy, found, err := h.store.GetToolPolicy(); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to read tool policy"})
		return
	} else if found {
		writeJSON(w, http.StatusOK, toolPolicyEnvelope{Source: "managed", Applied: true, Policy: policy})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), toolPolicyAPITimeout)
	defer cancel()
	response, err := h.agentClient.GetToolPolicy(ctx)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "failed to load runtime tool policy"})
		return
	}

	writeJSON(w, http.StatusOK, toolPolicyEnvelope{
		Source:  "runtime_default",
		Applied: true,
		Policy:  agent.ToolPolicyFromProto(response.GetPolicy()),
	})
}

func (h *Handler) PutToolPolicy(w http.ResponseWriter, r *http.Request) {
	session, ok := h.requireAdminSession(w, r)
	if !ok {
		return
	}

	var request toolPolicyWriteRequest
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body", "code": "invalid_request"})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), toolPolicyAPITimeout)
	defer cancel()
	toolsResponse, err := h.agentClient.ListTools(ctx)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "failed to load tool catalog", "code": "tool_catalog_unavailable"})
		return
	}

	knownTools := make(map[string]struct{}, len(toolsResponse.GetItems()))
	for _, item := range toolsResponse.GetItems() {
		knownTools[strings.ToLower(strings.TrimSpace(item.GetName()))] = struct{}{}
	}

	normalized, unknownTools, validationErr := normalizeToolPolicyRequest(request, knownTools)
	if validationErr != "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": validationErr, "code": "invalid_policy"})
		return
	}
	if len(unknownTools) > 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"error":         "unknown tool names",
			"code":          "unknown_tools",
			"unknown_tools": unknownTools,
		})
		return
	}

	version := int64(1)
	if current, found, err := h.store.GetToolPolicy(); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to read tool policy"})
		return
	} else if found {
		version = current.Version + 1
	}

	policy := domain.ToolPolicy{
		RoleAllow:        normalized.RoleAllow,
		ApprovalRequired: normalized.ApprovalRequired,
		DisabledTools:    normalized.DisabledTools,
		Version:          version,
		UpdatedAt:        time.Now().UTC(),
		UpdatedBy:        session.Username,
		Description:      normalized.Description,
	}

	saved, err := h.store.UpsertToolPolicy(policy)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to save tool policy"})
		return
	}

	response, err := h.agentClient.ApplyToolPolicy(ctx, &agentv1.ApplyToolPolicyRequest{
		Policy: agent.ToolPolicyToProto(saved),
	})
	if err != nil || !response.GetApplied() {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "tool policy saved but runtime apply failed", "code": "runtime_apply_failed"})
		return
	}

	writeJSON(w, http.StatusOK, toolPolicyEnvelope{Source: "managed", Applied: true, Policy: saved})
}

func (h *Handler) ReloadToolPolicy(w http.ResponseWriter, r *http.Request) {
	if _, ok := h.requireAdminSession(w, r); !ok {
		return
	}

	policy, found, err := h.store.GetToolPolicy()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to read tool policy"})
		return
	}
	if !found {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "managed tool policy not found", "code": "tool_policy_not_found"})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), toolPolicyAPITimeout)
	defer cancel()
	response, err := h.agentClient.ApplyToolPolicy(ctx, &agentv1.ApplyToolPolicyRequest{
		Policy: agent.ToolPolicyToProto(policy),
	})
	if err != nil || !response.GetApplied() {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "failed to reload tool policy", "code": "runtime_apply_failed"})
		return
	}

	writeJSON(w, http.StatusOK, toolPolicyEnvelope{Source: "managed", Applied: true, Policy: policy})
}

func (h *Handler) ListAdminTools(w http.ResponseWriter, r *http.Request) {
	if _, ok := h.requireAdminSession(w, r); !ok {
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), toolPolicyAPITimeout)
	defer cancel()
	response, err := h.agentClient.ListTools(ctx)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "failed to list tools"})
		return
	}

	items := make([]toolDescriptorResponse, 0, len(response.GetItems()))
	for _, item := range response.GetItems() {
		items = append(items, toolDescriptorResponse{
			Name:              item.GetName(),
			Description:       item.GetDescription(),
			RiskLevel:         item.GetRiskLevel(),
			RequiresApproval:  item.GetRequiresApproval(),
			ProviderName:      item.GetProviderName(),
			CurrentlyDisabled: item.GetCurrentlyDisabled(),
			AllowedRoles:      append([]string{}, item.GetAllowedRoles()...),
		})
	}

	writeJSON(w, http.StatusOK, map[string]any{"items": items, "count": len(items)})
}

func normalizeToolPolicyRequest(
	request toolPolicyWriteRequest,
	knownTools map[string]struct{},
) (toolPolicyWriteRequest, []string, string) {
	if len(request.RoleAllow) == 0 {
		return toolPolicyWriteRequest{}, nil, "role_allow is required"
	}

	allowedRoles := map[string]struct{}{"user": {}, "admin": {}}
	roleAllow := make(map[string][]string, len(request.RoleAllow))
	unknown := map[string]struct{}{}
	for rawRole, tools := range request.RoleAllow {
		role := strings.ToLower(strings.TrimSpace(rawRole))
		if _, ok := allowedRoles[role]; !ok {
			return toolPolicyWriteRequest{}, nil, "role_allow only supports user and admin"
		}
		roleAllow[role] = normalizeToolNames(tools, knownTools, true, unknown)
	}
	if _, ok := roleAllow["user"]; !ok {
		return toolPolicyWriteRequest{}, nil, "role_allow.user is required"
	}
	if _, ok := roleAllow["admin"]; !ok {
		return toolPolicyWriteRequest{}, nil, "role_allow.admin is required"
	}

	approvalRequired := normalizeToolNames(request.ApprovalRequired, knownTools, false, unknown)
	disabledTools := normalizeToolNames(request.DisabledTools, knownTools, false, unknown)

	unknownTools := make([]string, 0, len(unknown))
	for tool := range unknown {
		unknownTools = append(unknownTools, tool)
	}
	sort.Strings(unknownTools)

	description := strings.TrimSpace(request.Description)
	if len([]rune(description)) > 500 {
		return toolPolicyWriteRequest{}, nil, "description is too long"
	}

	return toolPolicyWriteRequest{
		RoleAllow:        roleAllow,
		ApprovalRequired: approvalRequired,
		DisabledTools:    disabledTools,
		Description:      description,
	}, unknownTools, ""
}

func normalizeToolNames(
	raw []string,
	knownTools map[string]struct{},
	allowWildcard bool,
	unknown map[string]struct{},
) []string {
	seen := map[string]struct{}{}
	normalized := make([]string, 0, len(raw))
	for _, item := range raw {
		value := strings.ToLower(strings.TrimSpace(item))
		if value == "" {
			continue
		}
		if value == "*" && allowWildcard {
			if _, exists := seen[value]; !exists {
				seen[value] = struct{}{}
				normalized = append(normalized, value)
			}
			continue
		}
		if !toolPolicyNamePattern.MatchString(value) {
			unknown[value] = struct{}{}
			continue
		}
		if _, exists := knownTools[value]; !exists {
			unknown[value] = struct{}{}
			continue
		}
		if _, exists := seen[value]; exists {
			continue
		}
		seen[value] = struct{}{}
		normalized = append(normalized, value)
	}
	sort.Strings(normalized)
	return normalized
}
