package agentinfo

import (
	"encoding/json"
	"fmt"
	"reflect"
	"strings"

	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
)

const (
	SchemaLegacyInfo = "synapse.agent.info.v1"
	SchemaV2         = "synapse.agent.event.v2"
)

type AgentInfo struct {
	SchemaVersion string         `json:"schema_version,omitempty"`
	EventName     string         `json:"event_name,omitempty"`
	Payload       map[string]any `json:"payload,omitempty"`
}

type legacyEnvelope struct {
	Schema      string         `json:"schema"`
	AgentEvent  string         `json:"agent_event"`
	Display     string         `json:"display_message"`
	Payload     map[string]any `json:"payload"`
	SchemaV2    string         `json:"schema_version"`
	EventNameV2 string         `json:"event_name"`
	PayloadV2   map[string]any `json:"payload_v2"`
}

func FromProtoOrLegacy(event *agentv1.AgentEvent) (AgentInfo, []string, bool) {
	if typed, ok := FromProto(event); ok {
		legacy, legacyOK := FromLegacyMessage(event.GetMessage())
		if legacyOK {
			return typed, ConflictDiagnostics(typed, legacy), true
		}
		return typed, nil, true
	}

	info, ok := FromLegacyMessage(event.GetMessage())
	return info, nil, ok
}

func FromProto(event *agentv1.AgentEvent) (AgentInfo, bool) {
	if event == nil || event.GetTypedPayload() == nil {
		return AgentInfo{}, false
	}

	schemaVersion := strings.TrimSpace(event.GetSchemaVersion())
	if schemaVersion == "" {
		schemaVersion = SchemaV2
	}

	switch payload := event.GetTypedPayload().(type) {
	case *agentv1.AgentEvent_Perceive:
		return newInfo(schemaVersion, "perceive", map[string]any{
			"task_id":               payload.Perceive.GetTaskId(),
			"short_context_count":   payload.Perceive.GetShortContextCount(),
			"recalled_memory_count": payload.Perceive.GetRecalledMemoryCount(),
		}), true
	case *agentv1.AgentEvent_Plan:
		return newInfo(schemaVersion, "plan", map[string]any{
			"step_count": payload.Plan.GetStepCount(),
			"steps":      stringSlice(payload.Plan.GetSteps()),
		}), true
	case *agentv1.AgentEvent_ToolSelected:
		return newInfo(schemaVersion, "tool_selected", toolCommonPayload(payload.ToolSelected)), true
	case *agentv1.AgentEvent_ToolStarted:
		return newInfo(schemaVersion, "tool_started", toolCommonPayload(payload.ToolStarted)), true
	case *agentv1.AgentEvent_ToolFinished:
		return newInfo(schemaVersion, "tool_finished", toolFinishedPayload(payload.ToolFinished)), true
	case *agentv1.AgentEvent_ToolFailed:
		return newInfo(schemaVersion, "tool_failed", toolFinishedPayload(payload.ToolFailed)), true
	case *agentv1.AgentEvent_ToolSkipped:
		values := toolCommonPayload(payload.ToolSkipped)
		values["reason"] = payload.ToolSkipped.GetReason()
		return newInfo(schemaVersion, "tool_skipped", values), true
	case *agentv1.AgentEvent_ApprovalRequired:
		return newInfo(schemaVersion, "approval_required", approvalPayload(payload.ApprovalRequired)), true
	case *agentv1.AgentEvent_MemoryRecall:
		return newInfo(schemaVersion, "memory_recall", memoryRecallPayload(payload.MemoryRecall)), true
	case *agentv1.AgentEvent_MemoryWrite:
		return newInfo(schemaVersion, "memory_write", map[string]any{
			"memory_id":       payload.MemoryWrite.GetMemoryId(),
			"user_id":         payload.MemoryWrite.GetUserId(),
			"summary":         payload.MemoryWrite.GetSummary(),
			"content_preview": payload.MemoryWrite.GetContentPreview(),
			"source_task_id":  payload.MemoryWrite.GetSourceTaskId(),
			"importance":      payload.MemoryWrite.GetImportance(),
			"created_at":      payload.MemoryWrite.GetCreatedAt(),
		}), true
	case *agentv1.AgentEvent_Evaluation:
		return newInfo(schemaVersion, "evaluate", map[string]any{
			"estimated_success":    payload.Evaluation.GetEstimatedSuccess(),
			"objective_completion": payload.Evaluation.GetObjectiveCompletion(),
			"tool_success_rate":    payload.Evaluation.GetToolSuccessRate(),
			"blocked_actions":      payload.Evaluation.GetBlockedActions(),
			"duration_ms":          payload.Evaluation.GetDurationMs(),
		}), true
	case *agentv1.AgentEvent_Replan:
		return newInfo(schemaVersion, "replan", map[string]any{
			"step_index":    payload.Replan.GetStepIndex(),
			"reason":        payload.Replan.GetReason(),
			"from_tool":     payload.Replan.GetFromTool(),
			"to_tool":       payload.Replan.GetToTool(),
			"to_tool_input": payload.Replan.GetToToolInput(),
		}), true
	case *agentv1.AgentEvent_SynthesisMode:
		return newInfo(schemaVersion, "synthesis_mode", map[string]any{
			"mode": payload.SynthesisMode.GetMode(),
		}), true
	default:
		return AgentInfo{}, false
	}
}

func FromLegacyMessage(message string) (AgentInfo, bool) {
	trimmed := strings.TrimSpace(message)
	if trimmed == "" {
		return AgentInfo{}, false
	}

	var envelope legacyEnvelope
	if err := json.Unmarshal([]byte(trimmed), &envelope); err != nil {
		return AgentInfo{}, false
	}

	eventName := strings.TrimSpace(envelope.AgentEvent)
	payload := envelope.Payload
	schema := strings.TrimSpace(envelope.Schema)
	if eventName == "" {
		eventName = strings.TrimSpace(envelope.EventNameV2)
	}
	if schema == "" {
		schema = strings.TrimSpace(envelope.SchemaV2)
	}
	if payload == nil {
		payload = envelope.PayloadV2
	}
	if eventName == "" {
		return AgentInfo{}, false
	}
	if schema == "" {
		schema = SchemaLegacyInfo
	}
	if payload == nil {
		payload = map[string]any{}
	}

	return newInfo(schema, eventName, payload), true
}

func ConflictDiagnostics(typed AgentInfo, legacy AgentInfo) []string {
	if typed.EventName == "" || legacy.EventName == "" {
		return nil
	}

	diagnostics := make([]string, 0)
	if typed.EventName != legacy.EventName {
		diagnostics = append(diagnostics, fmt.Sprintf(
			"event_name typed=%q legacy=%q",
			typed.EventName,
			legacy.EventName,
		))
	}

	for _, key := range []string{
		"step_index",
		"resume_step_index",
		"tool",
		"tool_name",
		"tool_input",
		"risk_level",
		"tool_call_id",
		"ok",
	} {
		typedValue, typedOK := typed.Payload[key]
		legacyValue, legacyOK := legacy.Payload[key]
		if !typedOK || !legacyOK {
			continue
		}
		if !reflect.DeepEqual(normalizeComparableValue(typedValue), normalizeComparableValue(legacyValue)) {
			diagnostics = append(diagnostics, fmt.Sprintf(
				"payload.%s typed=%q legacy=%q",
				key,
				typedValue,
				legacyValue,
			))
		}
	}

	return diagnostics
}

func newInfo(schemaVersion string, eventName string, payload map[string]any) AgentInfo {
	if payload == nil {
		payload = map[string]any{}
	}
	return AgentInfo{
		SchemaVersion: schemaVersion,
		EventName:     eventName,
		Payload:       payload,
	}
}

type toolCommon interface {
	GetStepIndex() int32
	GetObjective() string
	GetToolName() string
	GetToolCallId() string
	GetRiskLevel() string
	GetInputPreview() string
	GetRequiresApproval() bool
	GetProviderName() string
}

func toolCommonPayload(event toolCommon) map[string]any {
	inputPreview := event.GetInputPreview()
	toolName := event.GetToolName()
	return map[string]any{
		"step_index":        event.GetStepIndex(),
		"objective":         event.GetObjective(),
		"tool":              toolName,
		"tool_name":         toolName,
		"tool_input":        inputPreview,
		"input_preview":     inputPreview,
		"tool_call_id":      event.GetToolCallId(),
		"risk_level":        event.GetRiskLevel(),
		"requires_approval": event.GetRequiresApproval(),
		"provider_name":     event.GetProviderName(),
		"tool_provider":     event.GetProviderName(),
	}
}

type toolFinished interface {
	toolCommon
	GetOutputPreview() string
	GetDurationMs() int64
	GetOk() bool
	GetErrorCode() string
	GetErrorMessage() string
}

func toolFinishedPayload(event toolFinished) map[string]any {
	values := toolCommonPayload(event)
	errorCode := event.GetErrorCode()
	errorMessage := event.GetErrorMessage()
	values["output_preview"] = event.GetOutputPreview()
	values["duration_ms"] = event.GetDurationMs()
	values["ok"] = event.GetOk()
	values["error_code"] = errorCode
	values["error_message"] = errorMessage
	if errorCode != "" || errorMessage != "" {
		values["error"] = map[string]any{
			"code":    errorCode,
			"message": errorMessage,
		}
	}
	return values
}

func approvalPayload(event *agentv1.ApprovalRequiredEvent) map[string]any {
	approvedToolCall := map[string]any{}
	if event.GetApprovedToolCall() != nil {
		approved := event.GetApprovedToolCall()
		approvedToolCall = map[string]any{
			"tool_name":         approved.GetToolName(),
			"tool":              approved.GetToolName(),
			"tool_input":        approved.GetToolInput(),
			"risk_level":        approved.GetRiskLevel(),
			"reason":            approved.GetReason(),
			"resume_step_index": approved.GetResumeStepIndex(),
		}
	}

	return map[string]any{
		"step_index":         event.GetStepIndex(),
		"resume_step_index":  event.GetResumeStepIndex(),
		"objective":          event.GetObjective(),
		"tool":               event.GetToolName(),
		"tool_name":          event.GetToolName(),
		"tool_input":         event.GetToolInput(),
		"risk_level":         event.GetRiskLevel(),
		"reason":             event.GetReason(),
		"approval_reason":    event.GetApprovalReason(),
		"tool_call_id":       event.GetToolCallId(),
		"approved_tool_call": approvedToolCall,
	}
}

func memoryRecallPayload(event *agentv1.MemoryRecallEvent) map[string]any {
	hits := make([]any, 0, len(event.GetHits()))
	for _, hit := range event.GetHits() {
		hits = append(hits, map[string]any{
			"memory_id":       hit.GetMemoryId(),
			"summary":         hit.GetSummary(),
			"content_preview": hit.GetContentPreview(),
			"source_task_id":  hit.GetSourceTaskId(),
			"importance":      hit.GetImportance(),
			"score":           hit.GetScore(),
			"matched_terms":   stringSlice(hit.GetMatchedTerms()),
			"created_at":      hit.GetCreatedAt(),
		})
	}
	return map[string]any{
		"query":     event.GetQuery(),
		"hit_count": event.GetHitCount(),
		"hits":      hits,
	}
}

func stringSlice(values []string) []string {
	result := make([]string, len(values))
	copy(result, values)
	return result
}

func normalizeComparableValue(value any) any {
	switch typed := value.(type) {
	case float64:
		if typed == float64(int64(typed)) {
			return int64(typed)
		}
	case float32:
		if typed == float32(int64(typed)) {
			return int64(typed)
		}
	case int32:
		return int64(typed)
	case int:
		return int64(typed)
	case int64:
		return typed
	}
	return value
}
