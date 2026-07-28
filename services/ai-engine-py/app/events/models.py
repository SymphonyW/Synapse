from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.events.schema import AGENT_INFO_SCHEMA


@dataclass(frozen=True)
class AgentInfoEvent:
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    display_message: str = ""
    schema: str = AGENT_INFO_SCHEMA


@dataclass(frozen=True)
class ToolCallPayload:
    call_id: str
    tool_name: str
    input_text: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolEventPayload:
    step_index: int
    tool: str
    tool_input: str
    objective: str = ""
    tool_call: ToolCallPayload | Mapping[str, Any] | None = None
    tool_call_id: str = ""
    tool_provider: str = ""
    tool_description: str = ""
    input_schema: Mapping[str, Any] | None = None
    risk_level: str = ""
    requires_approval: bool | None = None
    reason: str = ""
    duration_ms: int | None = None
    ok: bool | None = None
    output: str = ""
    output_preview: str = ""
    error: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ApprovalRequiredPayload:
    step_index: int
    resume_step_index: int
    tool: str
    tool_input: str
    risk_level: str
    reason: str
    approval_reason: str
    approved_tool_call: Mapping[str, Any]
    tool_name: str = ""
    hint: str = "call task approve endpoint to resume execution"


@dataclass(frozen=True)
class MemoryRecallHitPayload:
    memory_id: str
    summary: str
    content_preview: str
    source_task_id: str
    importance: float
    created_at: int
    score: float
    matched_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MemoryRecallPayload:
    query: str
    hit_count: int
    hits: list[MemoryRecallHitPayload] = field(default_factory=list)


@dataclass(frozen=True)
class MemoryWritePayload:
    memory_id: str
    user_id: str
    summary: str
    content_preview: str
    source_task_id: str
    importance: float
    created_at: int


@dataclass(frozen=True)
class EvaluationPayload:
    estimated_success: float
    objective_completion: float
    tool_success_rate: float
    blocked_actions: int
    duration_ms: int
