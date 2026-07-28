from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.events.models import AgentInfoEvent
from app.events.schema import (
    AGENT_INFO_SCHEMA,
    MEMORY_PREVIEW_CHARS,
    SYNTHESIS_ERROR_PREVIEW_CHARS,
    TOOL_OUTPUT_PREVIEW_CHARS,
)
from app.events.serializer import to_jsonable


@dataclass(frozen=True)
class AgentEventFactory:
    schema: str = AGENT_INFO_SCHEMA
    tool_output_preview_chars: int = TOOL_OUTPUT_PREVIEW_CHARS
    memory_preview_chars: int = MEMORY_PREVIEW_CHARS
    synthesis_error_preview_chars: int = SYNTHESIS_ERROR_PREVIEW_CHARS

    def info(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        display_message: str = "",
    ) -> AgentInfoEvent:
        return AgentInfoEvent(
            event_type=str(event_type).strip(),
            payload=self._payload(payload or {}),
            display_message=str(display_message or "").strip(),
            schema=self.schema,
        )

    def perceive(
        self,
        *,
        task_id: str,
        short_context_count: int,
        recalled_memory_count: int,
    ) -> AgentInfoEvent:
        return self.info(
            "perceive",
            {
                "task_id": str(task_id or ""),
                "short_context_count": self._int(short_context_count),
                "recalled_memory_count": self._int(recalled_memory_count),
            },
        )

    def memory_recall(
        self,
        *,
        query: str,
        hits: Iterable[Mapping[str, Any]],
        display_message: str | None = None,
    ) -> AgentInfoEvent:
        normalized_hits = [self._memory_hit_payload(item) for item in hits]
        hit_count = len(normalized_hits)
        return self.info(
            "memory_recall",
            {
                "query": self._preview(query, self.memory_preview_chars),
                "hit_count": hit_count,
                "hits": normalized_hits,
            },
            display_message
            if display_message is not None
            else f"Memory recall: {hit_count} hit(s)",
        )

    def plan(self, *, steps: Iterable[str]) -> AgentInfoEvent:
        normalized_steps = [str(step or "") for step in steps]
        return self.info(
            "plan",
            {
                "step_count": len(normalized_steps),
                "steps": normalized_steps,
            },
        )

    def resume_started(self, *, resume_step_index: int) -> AgentInfoEvent:
        return self.info(
            "resume_started",
            {"resume_step_index": self._positive_step_index(resume_step_index)},
        )

    def step_started(self, *, step_index: int, objective: str) -> AgentInfoEvent:
        return self.info(
            "act",
            {
                "step_index": self._positive_step_index(step_index),
                "objective": str(objective or ""),
            },
        )

    def act(self, *, step_index: int, objective: str) -> AgentInfoEvent:
        return self.step_started(step_index=step_index, objective=objective)

    def tool_selected(
        self,
        payload: Mapping[str, Any],
        display_message: str | None = None,
    ) -> AgentInfoEvent:
        return self._tool_event(
            "tool_selected",
            payload,
            display_message=display_message,
            display_prefix="Tool selected",
        )

    def decide(
        self,
        *,
        step_index: int,
        tool: str,
        tool_input: str,
        planner: str,
        reason: str,
    ) -> AgentInfoEvent:
        return self.info(
            "decide",
            {
                "step_index": self._positive_step_index(step_index),
                "tool": str(tool or ""),
                "tool_input": str(tool_input or ""),
                "planner": str(planner or ""),
                "reason": str(reason or ""),
            },
        )

    def tool_started(
        self,
        payload: Mapping[str, Any],
        display_message: str | None = None,
    ) -> AgentInfoEvent:
        return self._tool_event(
            "tool_started",
            payload,
            display_message=display_message,
            display_prefix="Tool started",
        )

    def tool_finished(
        self,
        payload: Mapping[str, Any],
        display_message: str | None = None,
    ) -> AgentInfoEvent:
        return self._tool_event(
            "tool_finished",
            payload,
            display_message=display_message,
            display_prefix="Tool finished",
        )

    def tool_failed(
        self,
        payload: Mapping[str, Any],
        display_message: str | None = None,
    ) -> AgentInfoEvent:
        return self._tool_event(
            "tool_failed",
            payload,
            display_message=display_message,
            display_prefix="Tool failed",
        )

    def tool_skipped(
        self,
        payload: Mapping[str, Any],
        display_message: str | None = None,
    ) -> AgentInfoEvent:
        return self._tool_event(
            "tool_skipped",
            payload,
            display_message=display_message,
            display_prefix="Tool skipped",
        )

    def policy_blocked(self, *, step_index: int, tool: str, role: str) -> AgentInfoEvent:
        return self.info(
            "policy_blocked",
            {
                "step_index": self._positive_step_index(step_index),
                "tool": str(tool or ""),
                "role": str(role or ""),
            },
        )

    def approval_required(
        self,
        payload: Mapping[str, Any] | None = None,
        *,
        step_index: int | None = None,
        tool: str = "",
        tool_input: str = "",
        risk_level: str = "",
        reason: str = "approval_required",
        approval_reason: str = "",
        resume_step_index: int | None = None,
        approved_tool_call: Mapping[str, Any] | None = None,
        display_message: str | None = None,
    ) -> AgentInfoEvent:
        merged = dict(self._payload(payload or {}))
        if step_index is not None:
            merged["step_index"] = self._positive_step_index(step_index)
        if tool:
            merged["tool"] = str(tool)
        if "tool" in merged and "tool_name" not in merged:
            merged["tool_name"] = str(merged["tool"])
        if tool_input:
            merged["tool_input"] = str(tool_input)
        if risk_level:
            merged["risk_level"] = str(risk_level)
        if reason:
            merged["reason"] = str(reason)
        if approval_reason:
            merged["approval_reason"] = str(approval_reason)
        if resume_step_index is not None:
            merged["resume_step_index"] = self._positive_step_index(resume_step_index)
        if approved_tool_call is not None:
            merged["approved_tool_call"] = self._payload(approved_tool_call)
        if "hint" not in merged:
            merged["hint"] = "call task approve endpoint to resume execution"

        tool_name = str(merged.get("tool_name") or merged.get("tool") or "")
        return self.info(
            "approval_required",
            merged,
            display_message
            if display_message is not None
            else f"Approval required for tool: {tool_name}",
        )

    def paused(self, *, reason: str, tool: str, resume_step_index: int) -> AgentInfoEvent:
        return self.info(
            "paused",
            {
                "reason": str(reason or ""),
                "tool": str(tool or ""),
                "resume_step_index": self._positive_step_index(resume_step_index),
            },
        )

    def observation(self, payload: Mapping[str, Any]) -> AgentInfoEvent:
        return self.info("observe", self._payload(payload))

    def observe(self, payload: Mapping[str, Any]) -> AgentInfoEvent:
        return self.observation(payload)

    def reflection(
        self,
        *,
        step_index: int,
        reflection: str,
        replanned: bool = False,
    ) -> AgentInfoEvent:
        payload: dict[str, Any] = {
            "step_index": self._positive_step_index(step_index),
            "reflection": str(reflection or ""),
        }
        if replanned:
            payload["replanned"] = True
        return self.info("reflect", payload)

    def reflect(
        self,
        *,
        step_index: int,
        reflection: str,
        replanned: bool = False,
    ) -> AgentInfoEvent:
        return self.reflection(
            step_index=step_index,
            reflection=reflection,
            replanned=replanned,
        )

    def replan(
        self,
        *,
        step_index: int,
        reason: str,
        from_tool: str,
        to_tool: str,
        to_tool_input: str,
        display_message: str | None = None,
    ) -> AgentInfoEvent:
        return self.info(
            "replan",
            {
                "step_index": self._positive_step_index(step_index),
                "reason": str(reason or ""),
                "from_tool": str(from_tool or ""),
                "to_tool": str(to_tool or ""),
                "to_tool_input": str(to_tool_input or ""),
            },
            display_message
            if display_message is not None
            else f"Replanned step {self._positive_step_index(step_index)}: {reason}",
        )

    def synthesis_mode(self, *, mode: str) -> AgentInfoEvent:
        return self.info("synthesis_mode", {"mode": str(mode or "")})

    def synthesis_failed(self, *, error: str) -> AgentInfoEvent:
        return self.info(
            "synthesis_failed",
            {"error": self._preview(error, self.synthesis_error_preview_chars)},
        )

    def memory_write(
        self,
        *,
        memory_id: str,
        user_id: str,
        summary: str,
        content: str,
        source_task_id: str,
        importance: float,
        created_at: int,
        display_message: str = "Memory written",
    ) -> AgentInfoEvent:
        return self.info(
            "memory_write",
            {
                "memory_id": str(memory_id or ""),
                "user_id": str(user_id or ""),
                "summary": self._preview(summary, self.memory_preview_chars),
                "content_preview": self._preview(content, self.memory_preview_chars),
                "source_task_id": str(source_task_id or ""),
                "importance": self._float(importance),
                "created_at": self._int(created_at),
            },
            display_message,
        )

    def evaluation(
        self,
        *,
        estimated_success: float,
        objective_completion: float,
        tool_success_rate: float,
        blocked_actions: int,
        duration_ms: int,
    ) -> AgentInfoEvent:
        return self.info(
            "evaluate",
            {
                "estimated_success": self._float(estimated_success),
                "objective_completion": self._float(objective_completion),
                "tool_success_rate": self._float(tool_success_rate),
                "blocked_actions": self._int(blocked_actions),
                "duration_ms": max(0, self._int(duration_ms)),
            },
        )

    def evaluate(
        self,
        *,
        estimated_success: float,
        objective_completion: float,
        tool_success_rate: float,
        blocked_actions: int,
        duration_ms: int,
    ) -> AgentInfoEvent:
        return self.evaluation(
            estimated_success=estimated_success,
            objective_completion=objective_completion,
            tool_success_rate=tool_success_rate,
            blocked_actions=blocked_actions,
            duration_ms=duration_ms,
        )

    def diagnostic(
        self,
        *,
        message: str,
        payload: Mapping[str, Any] | None = None,
    ) -> AgentInfoEvent:
        return self.info("diagnostic", self._payload(payload or {}), message)

    def _tool_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        display_message: str | None,
        display_prefix: str,
    ) -> AgentInfoEvent:
        normalized = self._tool_payload(payload)
        tool_name = str(normalized.get("tool") or normalized.get("tool_name") or "")
        default_display = (
            f"{display_prefix}: {tool_name}" if tool_name else display_prefix
        )
        return self.info(
            event_type,
            normalized,
            display_message if display_message is not None else default_display,
        )

    def _tool_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(self._payload(payload))
        if "step_index" in normalized:
            normalized["step_index"] = self._positive_step_index(normalized["step_index"])
        if "duration_ms" in normalized:
            normalized["duration_ms"] = max(0, self._int(normalized["duration_ms"]))
        if "tool" in normalized and "tool_name" not in normalized:
            tool_call = normalized.get("tool_call")
            if not isinstance(tool_call, Mapping):
                normalized["tool_name"] = str(normalized["tool"])
        if "tool_input" in normalized:
            normalized["tool_input"] = str(normalized.get("tool_input") or "")
        if "output" in normalized:
            normalized["output"] = str(normalized.get("output") or "")
            normalized["output_preview"] = self._preview(
                normalized.get("output_preview", normalized["output"]),
                self.tool_output_preview_chars,
            )
        elif "output_preview" in normalized:
            normalized["output_preview"] = self._preview(
                normalized["output_preview"],
                self.tool_output_preview_chars,
            )
        return normalized

    def _memory_hit_payload(self, hit: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "memory_id": str(hit.get("memory_id", "") or ""),
            "summary": self._preview(hit.get("summary", ""), self.memory_preview_chars),
            "content_preview": self._preview(
                hit.get("content_preview", hit.get("content", "")),
                self.memory_preview_chars,
            ),
            "source_task_id": str(hit.get("source_task_id", "") or ""),
            "importance": self._float(hit.get("importance", 0.0)),
            "created_at": self._int(hit.get("created_at", 0)),
            "score": self._float(hit.get("score", 0.0)),
            "matched_terms": [
                str(item)
                for item in to_jsonable(hit.get("matched_terms", []))
                if isinstance(item, (str, int, float, bool))
            ],
        }

    def _payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = to_jsonable(payload)
        if not isinstance(normalized, dict):
            return {"value": normalized}
        return normalized

    def _preview(self, value: Any, limit: int) -> str:
        return str(value or "")[: max(0, int(limit))]

    def _positive_step_index(self, value: Any) -> int:
        return max(1, self._int(value))

    def _int(self, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
