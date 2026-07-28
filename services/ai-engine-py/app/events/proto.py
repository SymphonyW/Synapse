from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.events.models import AgentInfoEvent
from app.events.schema import AGENT_EVENT_V2_SCHEMA
from app.events.serializer import to_jsonable
from synapse.v1 import agent_pb2


def parse_legacy_info_event(message: str) -> AgentInfoEvent | None:
    try:
        decoded = json.loads(message)
    except (TypeError, json.JSONDecodeError):
        return None

    if not isinstance(decoded, Mapping):
        return None

    event_type = _str(decoded.get("agent_event"))
    if not event_type:
        return None

    payload = decoded.get("payload")
    return AgentInfoEvent(
        event_type=event_type,
        payload=payload if isinstance(payload, Mapping) else {},
        display_message=_str(decoded.get("display_message")),
        schema=_str(decoded.get("schema")),
    )


def to_proto_payload(event: AgentInfoEvent) -> tuple[str, Any | None]:
    payload = _mapping(to_jsonable(event.payload))
    event_type = str(event.event_type or "").strip()

    if event_type == "perceive":
        return event_type, agent_pb2.PerceiveEvent(
            task_id=_str(payload.get("task_id")),
            short_context_count=_int(payload.get("short_context_count")),
            recalled_memory_count=_int(payload.get("recalled_memory_count")),
        )

    if event_type == "plan":
        steps = payload.get("steps", [])
        return event_type, agent_pb2.PlanEvent(
            step_count=_int(payload.get("step_count")),
            steps=[_str(item) for item in steps] if isinstance(steps, list) else [],
        )

    if event_type == "tool_selected":
        return event_type, agent_pb2.ToolSelectedEvent(**_tool_common(payload))

    if event_type == "tool_started":
        return event_type, agent_pb2.ToolStartedEvent(**_tool_common(payload))

    if event_type == "tool_finished":
        return event_type, agent_pb2.ToolFinishedEvent(**_tool_finished(payload))

    if event_type == "tool_failed":
        values = _tool_finished(payload)
        if "ok" not in payload:
            values["ok"] = False
        return event_type, agent_pb2.ToolFailedEvent(**values)

    if event_type == "tool_skipped":
        values = _tool_common(payload)
        values["reason"] = _str(payload.get("reason"))
        return event_type, agent_pb2.ToolSkippedEvent(**values)

    if event_type == "approval_required":
        approved = _mapping(payload.get("approved_tool_call"))
        approved_message = None
        if approved:
            approved_message = agent_pb2.ApprovedToolCallEvent(
                tool_name=_tool_name(approved),
                tool_input=_str(approved.get("tool_input")),
                risk_level=_str(approved.get("risk_level")),
                reason=_str(approved.get("reason")),
                resume_step_index=_int(approved.get("resume_step_index")),
            )

        message = agent_pb2.ApprovalRequiredEvent(
            step_index=_int(payload.get("step_index")),
            resume_step_index=_int(payload.get("resume_step_index")),
            tool_name=_tool_name(payload),
            tool_input=_str(payload.get("tool_input")),
            risk_level=_str(payload.get("risk_level")),
            reason=_str(payload.get("reason")),
            tool_call_id=_tool_call_id(payload),
            approval_reason=_str(payload.get("approval_reason")),
            objective=_str(payload.get("objective")),
        )
        if approved_message is not None:
            message.approved_tool_call.CopyFrom(approved_message)
        return event_type, message

    if event_type == "memory_recall":
        hits = payload.get("hits", [])
        normalized_hits = []
        if isinstance(hits, list):
            normalized_hits = [
                agent_pb2.MemoryEventHit(
                    memory_id=_str(_mapping(hit).get("memory_id")),
                    summary=_str(_mapping(hit).get("summary")),
                    content_preview=_str(_mapping(hit).get("content_preview")),
                    source_task_id=_str(_mapping(hit).get("source_task_id")),
                    importance=_float(_mapping(hit).get("importance")),
                    score=_float(_mapping(hit).get("score")),
                    matched_terms=[
                        _str(term)
                        for term in _list(_mapping(hit).get("matched_terms"))
                    ],
                    created_at=_int(_mapping(hit).get("created_at")),
                )
                for hit in hits
                if isinstance(hit, Mapping)
            ]
        return event_type, agent_pb2.MemoryRecallEvent(
            query=_str(payload.get("query")),
            hit_count=_int(payload.get("hit_count", len(normalized_hits))),
            hits=normalized_hits,
        )

    if event_type == "memory_write":
        return event_type, agent_pb2.MemoryWriteEvent(
            memory_id=_str(payload.get("memory_id")),
            user_id=_str(payload.get("user_id")),
            summary=_str(payload.get("summary")),
            content_preview=_str(payload.get("content_preview")),
            source_task_id=_str(payload.get("source_task_id")),
            importance=_float(payload.get("importance")),
            created_at=_int(payload.get("created_at")),
        )

    if event_type == "evaluate":
        return event_type, agent_pb2.EvaluationEvent(
            estimated_success=_float(payload.get("estimated_success")),
            objective_completion=_float(payload.get("objective_completion")),
            tool_success_rate=_float(payload.get("tool_success_rate")),
            blocked_actions=_int(payload.get("blocked_actions")),
            duration_ms=_int(payload.get("duration_ms")),
        )

    if event_type == "replan":
        return event_type, agent_pb2.ReplanEvent(
            step_index=_int(payload.get("step_index")),
            reason=_str(payload.get("reason")),
            from_tool=_str(payload.get("from_tool")),
            to_tool=_str(payload.get("to_tool")),
            to_tool_input=_str(payload.get("to_tool_input")),
        )

    if event_type == "synthesis_mode":
        return event_type, agent_pb2.SynthesisModeEvent(mode=_str(payload.get("mode")))

    return event_type, None


def apply_typed_payload(proto_event: agent_pb2.AgentEvent, event: AgentInfoEvent) -> bool:
    _, payload = to_proto_payload(event)
    if payload is None:
        return False

    proto_event.schema_version = AGENT_EVENT_V2_SCHEMA
    field_name = _payload_field_name(event.event_type)
    getattr(proto_event, field_name).CopyFrom(payload)
    return True


def _payload_field_name(event_type: str) -> str:
    if event_type == "evaluate":
        return "evaluation"
    return event_type


def _tool_common(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "step_index": _int(payload.get("step_index")),
        "objective": _str(payload.get("objective")),
        "tool_name": _tool_name(payload),
        "tool_call_id": _tool_call_id(payload),
        "risk_level": _str(payload.get("risk_level")),
        "input_preview": _str(payload.get("input_preview", payload.get("tool_input"))),
        "requires_approval": _bool(payload.get("requires_approval")),
        "provider_name": _str(payload.get("provider_name", payload.get("tool_provider"))),
    }


def _tool_finished(payload: Mapping[str, Any]) -> dict[str, Any]:
    values = _tool_common(payload)
    error = _mapping(payload.get("error"))
    values.update(
        {
            "output_preview": _str(payload.get("output_preview", payload.get("output"))),
            "duration_ms": _int(payload.get("duration_ms")),
            "ok": _bool(payload.get("ok")),
            "error_code": _str(payload.get("error_code", error.get("code"))),
            "error_message": _str(
                payload.get("error_message", error.get("message", payload.get("reason")))
            ),
        }
    )
    return values


def _tool_name(payload: Mapping[str, Any]) -> str:
    return _str(payload.get("tool_name", payload.get("tool")))


def _tool_call_id(payload: Mapping[str, Any]) -> str:
    value = _str(payload.get("tool_call_id"))
    if value:
        return value
    tool_call = _mapping(payload.get("tool_call"))
    return _str(tool_call.get("call_id"))


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _str(value: Any) -> str:
    return str(value or "")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
