import asyncio
import ast
import html
import json
import re
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterator
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from app.memory import FileMemoryStore, MemoryRecord, MemoryStore
from app.tools import (
    ToolAuditLogger,
    ToolCall,
    ToolContext,
    ToolPolicy,
    ToolProvider,
    ToolRegistry,
    ToolResult,
    register_builtin_tools,
)


MODEL_MESSAGES_METADATA_KEY = "model_messages_json"
METADATA_AGENT_ENABLED_KEY = "agent_enabled"
METADATA_APPROVAL_GRANTED_KEY = "approval_granted"
METADATA_APPROVED_TOOLS_KEY = "approved_tools"
METADATA_APPROVED_TOOL_CALL_KEY = "approved_tool_call"
METADATA_AUTH_USER_ROLE_KEY = "auth_user_role"
METADATA_AUTH_USERNAME_KEY = "auth_username"
METADATA_MEMORY_WRITE_ENABLED_KEY = "memory_write_enabled"
METADATA_AGENT_RESUME_STEP_KEY = "agent_resume_step_index"
METADATA_AGENT_REQUIRED_TOOL_KEY = "agent_required_tool"
METADATA_AGENT_REQUIRED_TOOL_INPUT_KEY = "agent_required_tool_input"
METADATA_AGENT_RESUME_REQUESTED_BY_KEY = "agent_resume_requested_by"
METADATA_CLIENT_USER_MESSAGE_KEY = "user_message"
AGENT_INFO_SCHEMA = "synapse.agent.info.v1"
BROWSER_MAX_CONTENT_BYTES = 128 * 1024
BROWSER_OUTPUT_TEXT_LIMIT = 2400
BROWSER_USER_AGENT = "synapse-agent-browser/1.0"
OPENAI_DONE_MARKER = "[[SYNAPSE_DONE]]"
OPENAI_INTERRUPTED_FINISH_REASONS = {"content_filter", "safety", "sensitive", "blocked"}
OPENAI_TERMINAL_RESPONSE_CHARS = (
    ".",
    "!",
    "?",
    ";",
    "\u3002",
    "\uff01",
    "\uff1f",
    "\uff1b",
    "\u2026",
    "]",
    ")",
    "}",
    "\"",
    "'",
)
OPENAI_STRONG_TERMINAL_RESPONSE_CHARS = OPENAI_TERMINAL_RESPONSE_CHARS[:9]


@dataclass(frozen=True)
class RuntimeStreamItem:
    kind: str
    message: str = ""
    token: str = ""


@dataclass(frozen=True)
class OpenAIStreamItem:
    content: str = ""
    finish_reason: str = ""


@dataclass(frozen=True)
class OpenAICompletionResult:
    content: str = ""
    finish_reason: str = ""


class OpenAIDoneMarkerBuffer:
    def __init__(self, marker: str) -> None:
        self._marker = marker
        self._buffer = ""
        self.done = False

    def feed(self, text: str) -> list[str]:
        if self.done or not text:
            return []

        self._buffer += text
        marker_index = self._buffer.find(self._marker)
        if marker_index >= 0:
            visible = self._buffer[:marker_index]
            self._buffer = ""
            self.done = True
            return [visible] if visible else []

        keep = max(0, len(self._marker) - 1)
        if len(self._buffer) <= keep:
            return []

        visible = self._buffer[:-keep]
        self._buffer = self._buffer[-keep:]
        return [visible] if visible else []

    def flush(self) -> list[str]:
        if self.done:
            self._buffer = ""
            return []

        visible = self._buffer
        self._buffer = ""
        if self._marker.startswith(visible):
            return []
        return [visible] if visible else []


@dataclass(frozen=True)
class PlannedStep:
    index: int
    objective: str


@dataclass(frozen=True)
class AgentEvaluation:
    estimated_success: float
    objective_completion: float
    tool_success_rate: float
    blocked_actions: int


@dataclass(frozen=True)
class PlannerDecision:
    step_index: int
    objective: str
    tool_name: str
    tool_input: str
    planner: str
    reason: str = ""

    @property
    def uses_tool(self) -> bool:
        return self.tool_name != "none"


@dataclass(frozen=True)
class ToolExecutionOutcome:
    status: str
    observation: str
    result: ToolResult | None = None
    duration_ms: int = 0
    reason: str = ""
    completed: bool = False
    blocked: bool = False
    tool_called: bool = False
    tool_succeeded: bool = False
    should_pause: bool = False


@dataclass(frozen=True)
class ReplanDecision:
    should_replan: bool
    reason: str = ""
    decision: PlannerDecision | None = None


@dataclass(frozen=True)
class BrowserDocument:
    """浏览器工具内部流转的页面快照。

    这个结构不直接暴露给 Gateway，而是帮助 search/open/extract/summarize 共用同一份
    请求、解析和审计结果，避免每个工具重复实现网络安全边界。
    """

    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    raw_text: str
    text: str
    title: str
    byte_count: int
    truncated: bool


class AgentRuntime:
    """面向 provider 的运行时，按提示词生成 token 流。"""

    def __init__(
        self,
        model_provider: str,
        model_provider_alias: str = "",
        openai_api_key: str = "",
        openai_base_url: str = "",
        openai_model: str = "gpt-4o-mini",
        openai_temperature: float = 0.2,
        openai_max_tokens: int = 512,
        openai_http_timeout_seconds: float = 45.0,
        openai_max_retries: int = 3,
        openai_retry_backoff_seconds: float = 1.5,
        openai_continuation_max_rounds: int = 8,
        openai_long_form_min_chars: int = 2400,
        agent_enabled_default: bool = True,
        agent_max_plan_steps: int = 6,
        agent_generation_timeout_seconds: float = 30.0,
        agent_stream_idle_timeout_seconds: float = 15.0,
        agent_require_approval_for_high_risk: bool = True,
        agent_memory_file: str = "",
        agent_memory_max_entries_per_user: int = 80,
        agent_memory_recall_limit: int = 3,
        agent_tool_http_allowlist: tuple[str, ...] | list[str] | None = None,
        agent_tool_http_timeout_seconds: float = 12.0,
        agent_enable_code_execution: bool = False,
        agent_tool_policy_json: str = "",
        agent_tool_audit_log_file: str = "",
        agent_tool_providers: tuple[ToolProvider, ...] | list[ToolProvider] | None = None,
    ) -> None:
        raw_provider = model_provider.strip().lower() or "mock"
        alias = model_provider_alias.strip().lower()

        # 支持语义别名，同时复用同一条 OpenAI-compatible 传输路径。
        if raw_provider in {"zhipu", "gemini"}:
            alias = alias or raw_provider
            raw_provider = "openai"

        self.model_provider = raw_provider
        self.model_provider_display = alias or raw_provider
        self._openai_api_key = openai_api_key
        self._openai_base_url = openai_base_url
        self._openai_model = openai_model
        self._openai_temperature = openai_temperature
        self._openai_max_tokens = openai_max_tokens
        self._openai_http_timeout_seconds = max(5.0, openai_http_timeout_seconds)
        self._openai_max_retries = max(1, openai_max_retries)
        self._openai_retry_backoff_seconds = max(0.2, openai_retry_backoff_seconds)
        self._openai_continuation_max_rounds = max(0, openai_continuation_max_rounds)
        self._openai_long_form_min_chars = max(0, openai_long_form_min_chars)
        self._agent_generation_timeout_seconds = max(6.0, agent_generation_timeout_seconds)
        self._agent_stream_idle_timeout_seconds = max(2.0, agent_stream_idle_timeout_seconds)
        self._agent_rescue_timeout_seconds = min(
            self._agent_generation_timeout_seconds,
            max(8.0, self._agent_generation_timeout_seconds * 0.35),
        )
        self._agent_enabled_default = agent_enabled_default
        self._agent_max_plan_steps = max(1, agent_max_plan_steps)
        self._agent_require_approval_for_high_risk = agent_require_approval_for_high_risk
        self._agent_memory_recall_limit = max(1, agent_memory_recall_limit)
        self._agent_memory_store: MemoryStore = FileMemoryStore(
            file_path=agent_memory_file,
            max_entries_per_user=agent_memory_max_entries_per_user,
        )
        self._agent_tool_http_allowlist = {
            item.strip().lower()
            for item in (agent_tool_http_allowlist or [])
            if item.strip()
        }
        self._agent_tool_http_timeout_seconds = max(1.0, agent_tool_http_timeout_seconds)
        self._agent_enable_code_execution = agent_enable_code_execution
        self._tool_registry = ToolRegistry()
        register_builtin_tools(
            self._tool_registry,
            safe_eval=self._safe_eval_expression,
            fetch_http=self._execute_http_tool,
            enable_code_execution=self._agent_enable_code_execution,
            browse_web=self._execute_browser_tool,
        )
        for provider in agent_tool_providers or ():
            # 外部 provider 只负责发现工具，后续角色权限、审批和审计仍由 runtime 统一处理。
            self._tool_registry.register_provider(provider)

        default_approval_required: set[str] = self._tool_registry.default_approval_required()
        if self._agent_require_approval_for_high_risk:
            # 优先使用新的 requires_approval 声明，同时保留 high_risk
            # 作为兼容桥，支持仍只暴露旧字段的工具。
            for tool_name in self._tool_registry.names():
                tool = self._tool_registry.get(tool_name)
                if tool is not None and (
                    getattr(tool, "requires_approval", False)
                    or getattr(tool, "high_risk", False)
                ):
                    default_approval_required.add(tool_name)

        default_role_allow = {"admin": {"*"}}
        for role, tools in self._tool_registry.default_role_allow().items():
            default_role_allow.setdefault(role, set()).update(tools)

        self._tool_policy = ToolPolicy.from_json(
            raw_json=agent_tool_policy_json,
            default_role_allow=default_role_allow,
            default_approval_required=default_approval_required,
            default_disabled_tools=self._tool_registry.default_disabled_tools(),
        )
        self._tool_audit = ToolAuditLogger(agent_tool_audit_log_file)

        if self.model_provider == "openai":
            # OpenAI 模式必须显式配置 API Key。
            if not openai_api_key:
                raise ValueError(
                    "SYNAPSE_OPENAI_API_KEY is required when SYNAPSE_MODEL_PROVIDER=openai"
                )

        elif self.model_provider != "mock":
            raise ValueError(f"unsupported model provider: {self.model_provider}")

    async def run_prompt(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> AsyncIterator[str]:
        # provider 分流集中在此，保持 service 层与具体模型解耦。
        if self.model_provider == "openai":
            async for token in self._run_openai(prompt, metadata):
                yield token
            return

        async for token in self._run_mock(prompt):
            yield token

    async def run_task(
        self,
        task_id: str,
        user_id: str,
        prompt: str,
        metadata: dict[str, str] | None = None,
    ) -> AsyncIterator[RuntimeStreamItem]:
        metadata_map = dict(metadata or {})
        if not self._is_agent_enabled(metadata_map):
            provider_error = ""
            try:
                async for token in self._run_prompt_with_timeout(
                    prompt,
                    metadata_map,
                    timeout_seconds=self._agent_generation_timeout_seconds,
                ):
                    yield RuntimeStreamItem(kind="token", token=token)
            except Exception as exc:
                provider_error = str(exc)

            if provider_error:
                fallback = self._build_model_unavailable_response(provider_error)
                for chunk in self._chunk_text(fallback):
                    yield RuntimeStreamItem(kind="token", token=chunk)
            return

        started_at = time.time()
        normalized_prompt = " ".join(prompt.strip().split())
        if not normalized_prompt:
            normalized_prompt = "empty request"

        normalized_user_id = user_id.strip() or metadata_map.get(METADATA_AUTH_USERNAME_KEY, "")
        actor_role = self._normalize_role(metadata_map.get(METADATA_AUTH_USER_ROLE_KEY, "user"))
        approval_granted = self._read_bool(
            metadata_map.get(METADATA_APPROVAL_GRANTED_KEY), default_value=False
        )
        approved_tools = self._parse_csv_set(metadata_map.get(METADATA_APPROVED_TOOLS_KEY, ""))
        approved_tool_call = self._parse_approved_tool_call(metadata_map)
        resume_step_index = self._read_int(
            metadata_map.get(METADATA_AGENT_RESUME_STEP_KEY), default_value=1
        )
        if resume_step_index < 1:
            resume_step_index = 1

        short_context = self._extract_short_context(metadata_map)
        context_url = self._extract_latest_context_url(normalized_prompt, metadata_map)
        memory_hits = self._agent_memory_store.memory_recall(
            normalized_user_id, normalized_prompt, self._agent_memory_recall_limit
        )
        recalled_memories = [hit.to_dict() for hit in memory_hits]

        yield RuntimeStreamItem(
            kind="info",
            message=self._encode_agent_info(
                phase="perceive",
                payload={
                    "task_id": task_id,
                    "short_context_count": len(short_context),
                    "recalled_memory_count": len(recalled_memories),
                },
            ),
        )
        yield RuntimeStreamItem(
            kind="info",
            message=self._encode_agent_info(
                phase="memory_recall",
                payload={
                    "query": normalized_prompt[:240],
                    "hit_count": len(recalled_memories),
                    "hits": [
                        {
                            "memory_id": item.get("memory_id", ""),
                            "summary": str(item.get("summary", ""))[:240],
                            "content_preview": str(item.get("content", ""))[:240],
                            "source_task_id": item.get("source_task_id", ""),
                            "importance": item.get("importance", 0.0),
                            "created_at": item.get("created_at", 0),
                            "score": item.get("score", 0.0),
                            "matched_terms": item.get("matched_terms", []),
                        }
                        for item in recalled_memories
                    ],
                },
                display_message=f"Memory recall: {len(recalled_memories)} hit(s)",
            ),
        )

        plan_steps = self._build_plan_steps(normalized_prompt)
        yield RuntimeStreamItem(
            kind="info",
            message=self._encode_agent_info(
                phase="plan",
                payload={
                    "step_count": len(plan_steps),
                    "steps": [step.objective for step in plan_steps],
                },
            ),
        )

        completed_steps = max(0, resume_step_index - 1)
        tool_call_count = 0
        tool_success_count = 0
        blocked_actions = 0
        step_summaries: list[str] = []
        successful_tool_observations: list[tuple[str, str]] = []
        replan_used = False

        def record_outcome(outcome: ToolExecutionOutcome) -> None:
            nonlocal completed_steps, tool_call_count, tool_success_count, blocked_actions
            if outcome.completed:
                completed_steps += 1
            if outcome.blocked:
                blocked_actions += 1
            if outcome.tool_called:
                tool_call_count += 1
            if outcome.tool_succeeded:
                tool_success_count += 1

        if resume_step_index > 1:
            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="resume_started",
                    payload={
                        "resume_step_index": resume_step_index,
                    },
                ),
            )

        # 主循环显式分为 planner -> executor -> observer -> replanner。
        # mock provider 使用确定性 planner；openai provider 先预留模型选工具入口，
        # 当前仍会回退到同一套确定性策略，避免改变线上行为。
        for step in plan_steps:
            if step.index < resume_step_index:
                continue

            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="act",
                    payload={
                        "step_index": step.index,
                        "objective": step.objective,
                    },
                ),
            )

            decision = self._planner_select_tool(
                step=step,
                prompt=normalized_prompt,
                context_url=context_url,
                metadata=metadata_map,
            )
            for planner_event in self._planner_decision_events(decision):
                yield planner_event

            outcome_box: dict[str, ToolExecutionOutcome] = {}
            async for executor_event in self._executor_run_decision_events(
                decision=decision,
                task_id=task_id,
                user_id=normalized_user_id,
                user_role=actor_role,
                prompt=normalized_prompt,
                metadata=metadata_map,
                recalled_memories=recalled_memories,
                approval_granted=approval_granted,
                approved_tools=approved_tools,
                approved_tool_call=approved_tool_call,
                outcome_box=outcome_box,
            ):
                yield executor_event

            outcome = outcome_box["outcome"]
            if outcome.should_pause:
                return

            record_outcome(outcome)
            if outcome.tool_succeeded and outcome.result is not None:
                successful_tool_observations.append((decision.tool_name, outcome.result.output))

            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="observe",
                    payload=self._observer_payload(decision, outcome),
                ),
            )

            reflection = self._observer_reflect_decision(decision, outcome)
            step_summaries.append(reflection)
            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="reflect",
                    payload={
                        "step_index": step.index,
                        "reflection": reflection,
                    },
                ),
            )

            replan = self._replanner_after_failure(
                step=step,
                failed_decision=decision,
                outcome=outcome,
                prompt=normalized_prompt,
                context_url=context_url,
                already_replanned=replan_used,
            )
            if not replan.should_replan or replan.decision is None:
                continue

            replan_used = True
            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="replan",
                    payload={
                        "step_index": step.index,
                        "reason": replan.reason,
                        "from_tool": decision.tool_name,
                        "to_tool": replan.decision.tool_name,
                        "to_tool_input": replan.decision.tool_input,
                    },
                    display_message=f"Replanned step {step.index}: {replan.reason}",
                ),
            )

            for planner_event in self._planner_decision_events(replan.decision):
                yield planner_event

            replan_outcome_box: dict[str, ToolExecutionOutcome] = {}
            async for executor_event in self._executor_run_decision_events(
                decision=replan.decision,
                task_id=task_id,
                user_id=normalized_user_id,
                user_role=actor_role,
                prompt=normalized_prompt,
                metadata=metadata_map,
                recalled_memories=recalled_memories,
                approval_granted=approval_granted,
                approved_tools=approved_tools,
                approved_tool_call=approved_tool_call,
                outcome_box=replan_outcome_box,
            ):
                yield executor_event

            replan_outcome = replan_outcome_box["outcome"]
            if replan_outcome.should_pause:
                return

            record_outcome(replan_outcome)
            if replan_outcome.tool_succeeded and replan_outcome.result is not None:
                successful_tool_observations.append(
                    (replan.decision.tool_name, replan_outcome.result.output)
                )

            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="observe",
                    payload=self._observer_payload(
                        replan.decision,
                        replan_outcome,
                        replanned=True,
                    ),
                ),
            )

            reflection = self._observer_reflect_decision(replan.decision, replan_outcome)
            step_summaries.append(reflection)
            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="reflect",
                    payload={
                        "step_index": step.index,
                        "reflection": reflection,
                        "replanned": True,
                    },
                ),
            )

        evaluation = self._evaluate_task(
            total_steps=len(plan_steps),
            completed_steps=completed_steps,
            tool_call_count=tool_call_count,
            tool_success_count=tool_success_count,
            blocked_actions=blocked_actions,
        )

        final_response_chunks: list[str] = []
        synthesis_error = ""
        use_direct_generation = tool_call_count == 0 and blocked_actions == 0
        if self.model_provider == "mock":
            mock_answer = self._build_mock_user_facing_answer(
                prompt=normalized_prompt,
                step_summaries=step_summaries,
                evaluation=evaluation,
            )
            for chunk in self._chunk_text(mock_answer):
                final_response_chunks.append(chunk)
                yield RuntimeStreamItem(kind="token", token=chunk)
        else:
            generation_prompt = normalized_prompt
            generation_metadata: dict[str, str] | None = metadata_map
            generation_mode = "direct"
            if not use_direct_generation:
                generation_prompt = self._build_user_facing_prompt(
                    prompt=normalized_prompt,
                    short_context=short_context,
                    recalled_memories=recalled_memories,
                    step_summaries=step_summaries,
                    evaluation=evaluation,
                )
                generation_metadata = None
                generation_mode = "planner"

            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="synthesis_mode",
                    payload={
                        "mode": generation_mode,
                    },
                ),
            )

            try:
                async for chunk in self._run_prompt_with_timeout(
                    generation_prompt,
                    generation_metadata,
                    timeout_seconds=self._agent_generation_timeout_seconds,
                ):
                    if not chunk:
                        continue
                    final_response_chunks.append(chunk)
                    yield RuntimeStreamItem(kind="token", token=chunk)
            except Exception as exc:
                synthesis_error = str(exc)

        final_response = "".join(final_response_chunks).strip()
        synthesis_failed = final_response == ""
        if synthesis_failed and synthesis_error == "":
            synthesis_error = "empty synthesis response"

        if synthesis_failed:
            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="synthesis_failed",
                    payload={
                        "error": synthesis_error[:220],
                    },
                ),
            )

            # 暴露诊断兜底前，先尝试更短的直接生成路径。
            try:
                rescue_chunks: list[str] = []
                async for chunk in self._run_prompt_with_timeout(
                    normalized_prompt,
                    metadata_map,
                    timeout_seconds=self._agent_rescue_timeout_seconds,
                ):
                    if not chunk:
                        continue
                    rescue_chunks.append(chunk)

                rescued_response = "".join(rescue_chunks).strip()
                if rescued_response:
                    final_response = rescued_response
                    for chunk in self._chunk_text(rescued_response):
                        yield RuntimeStreamItem(kind="token", token=chunk)
                    synthesis_failed = False
            except Exception:
                pass

        if synthesis_failed:
            fallback_from_tools = self._build_tool_observation_fallback(
                prompt=normalized_prompt,
                tool_observations=successful_tool_observations,
                synthesis_error=synthesis_error,
            )
            if fallback_from_tools:
                final_response = fallback_from_tools
                for chunk in self._chunk_text(fallback_from_tools):
                    yield RuntimeStreamItem(kind="token", token=chunk)
                synthesis_failed = False

        if synthesis_failed:
            fallback_response = self._build_model_unavailable_response(synthesis_error)
            final_response = fallback_response
            for chunk in self._chunk_text(fallback_response):
                yield RuntimeStreamItem(kind="token", token=chunk)

        memory_write_enabled = self._read_bool(
            metadata_map.get(METADATA_MEMORY_WRITE_ENABLED_KEY), default_value=True
        )
        if memory_write_enabled:
            written_memory = self._agent_memory_store.memory_write(
                MemoryRecord(
                    memory_id="",
                    user_id=normalized_user_id,
                    content=self._build_memory_content(normalized_prompt, final_response),
                    summary=" | ".join(step_summaries[-3:]),
                    source_task_id=task_id,
                    importance=evaluation.estimated_success,
                    created_at=int(time.time() * 1000),
                )
            )
            if written_memory is not None:
                yield RuntimeStreamItem(
                    kind="info",
                    message=self._encode_agent_info(
                        phase="memory_write",
                        payload={
                            "memory_id": written_memory.memory_id,
                            "user_id": written_memory.user_id,
                            "summary": written_memory.summary[:240],
                            "content_preview": written_memory.content[:240],
                            "source_task_id": written_memory.source_task_id,
                            "importance": written_memory.importance,
                            "created_at": written_memory.created_at,
                        },
                        display_message="Memory written",
                    ),
                )

        elapsed_ms = int((time.time() - started_at) * 1000)
        yield RuntimeStreamItem(
            kind="info",
            message=self._encode_agent_info(
                phase="evaluate",
                payload={
                    "estimated_success": evaluation.estimated_success,
                    "objective_completion": evaluation.objective_completion,
                    "tool_success_rate": evaluation.tool_success_rate,
                    "blocked_actions": evaluation.blocked_actions,
                    "duration_ms": elapsed_ms,
                },
            ),
        )

    def memory_write(
        self,
        user_id: str,
        content: str,
        summary: str,
        source_task_id: str,
        importance: float,
    ) -> dict[str, Any] | None:
        written = self._agent_memory_store.memory_write(
            MemoryRecord(
                memory_id="",
                user_id=user_id,
                content=content,
                summary=summary,
                source_task_id=source_task_id,
                importance=importance,
                created_at=int(time.time() * 1000),
            )
        )
        if written is None:
            return None
        return written.to_dict()

    def memory_recall(self, user_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        return [
            hit.to_dict()
            for hit in self._agent_memory_store.memory_recall(user_id, query, limit)
        ]

    def memory_delete(self, user_id: str, memory_id: str) -> bool:
        return self._agent_memory_store.memory_delete(user_id, memory_id)

    def memory_list(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        return [
            record.to_dict()
            for record in self._agent_memory_store.memory_list(user_id, limit)
        ]

    def _build_memory_content(self, prompt: str, final_response: str) -> str:
        # 自动写入时保留用户请求和最终回复，summary 负责短摘要，content 负责后续召回的完整上下文。
        parts = []
        if prompt.strip():
            parts.append(f"User request: {prompt.strip()}")
        if final_response.strip():
            parts.append(f"Assistant response: {final_response.strip()[:1200]}")
        return "\n".join(parts)

    def _is_agent_enabled(self, metadata: dict[str, str]) -> bool:
        return self._read_bool(
            metadata.get(METADATA_AGENT_ENABLED_KEY),
            default_value=self._agent_enabled_default,
        )

    def _read_bool(self, value: str | None, default_value: bool) -> bool:
        if value is None:
            return default_value

        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "y"}:
            return True
        if normalized in {"0", "false", "no", "off", "n"}:
            return False

        return default_value

    def _read_int(self, value: str | None, default_value: int) -> int:
        if value is None:
            return default_value

        try:
            return int(value.strip())
        except (TypeError, ValueError):
            return default_value

    def _parse_csv_set(self, value: str) -> set[str]:
        if not value.strip():
            return set()

        return {item.strip().lower() for item in value.split(",") if item.strip()}

    def _normalize_role(self, value: str) -> str:
        normalized = value.strip().lower()
        if normalized == "admin":
            return "admin"
        return "user"

    def _extract_short_context(self, metadata: dict[str, str]) -> list[str]:
        raw_messages = metadata.get(MODEL_MESSAGES_METADATA_KEY, "").strip()
        if not raw_messages:
            return []

        try:
            parsed = json.loads(raw_messages)
        except json.JSONDecodeError:
            return []

        if not isinstance(parsed, list):
            return []

        extracted: list[str] = []
        for item in parsed[-6:]:
            if not isinstance(item, dict):
                continue

            role = item.get("role")
            content = item.get("content")
            if not isinstance(role, str) or not isinstance(content, str):
                continue

            normalized_content = " ".join(content.strip().split())
            if not normalized_content:
                continue

            extracted.append(f"{role}: {normalized_content[:180]}")

        return extracted

    def _encode_agent_info(
        self,
        phase: str,
        payload: dict[str, Any],
        display_message: str = "",
    ) -> str:
        # 保留旧版顶层 agent_event/payload 字段，同时增加 schema 标记和
        # 可选展示文案，供理解标准化工具事件的客户端使用。
        message = {
            "schema": AGENT_INFO_SCHEMA,
            "agent_event": phase,
            "payload": payload,
        }
        if display_message.strip():
            message["display_message"] = display_message.strip()
        return json.dumps(message, ensure_ascii=True, separators=(",", ":"))

    def _build_tool_event_payload(
        self,
        step_index: int,
        objective: str,
        tool_name: str,
        tool_input: str,
        reason: str = "",
        result: ToolResult | None = None,
        duration_ms: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # 标准工具事件 payload。这里有意保留旧版 `tool` 和 `tool_input`
        # 字段，确保 Gateway 暂停解析和旧前端仍能读取已知值。
        tool = self._tool_registry.get(tool_name)
        payload: dict[str, Any] = {
            "step_index": step_index,
            "objective": objective,
            "tool": tool_name,
            "tool_input": tool_input,
            "tool_call": {
                "tool_name": tool_name,
                "input_text": tool_input,
                "arguments": self._build_tool_call_arguments(tool_name, tool_input),
            },
        }

        if tool is not None:
            payload["tool_provider"] = self._tool_registry.provider_for(tool_name)
            payload["tool_description"] = tool.description
            payload["input_schema"] = tool.input_schema
            payload["risk_level"] = tool.risk_level
            payload["requires_approval"] = tool.requires_approval

        if reason:
            payload["reason"] = reason
        if duration_ms is not None:
            payload["duration_ms"] = max(0, duration_ms)
        if result is not None:
            payload["ok"] = result.ok
            payload["output"] = result.output
            payload["output_preview"] = result.output[:600]
            if result.metadata:
                payload["metadata"] = result.metadata
            if result.error is not None:
                payload["error"] = {
                    "code": result.error.code,
                    "message": result.error.message,
                    "retryable": result.error.retryable,
                    "details": result.error.details,
                }
        if extra:
            payload.update(extra)

        return payload

    def _planner_select_tool(
        self,
        step: PlannedStep,
        prompt: str,
        context_url: str,
        metadata: dict[str, str],
    ) -> PlannerDecision:
        # planner 负责把“下一步目标”变成可执行的工具决策。
        # mock 下必须保持确定性；openai 下先预留模型选工具入口，再回退到确定性策略。
        model_decision = self._planner_select_tool_with_model(
            step=step,
            prompt=prompt,
            context_url=context_url,
            metadata=metadata,
        )
        if model_decision is not None:
            return model_decision

        forced_tool = metadata.get(METADATA_AGENT_REQUIRED_TOOL_KEY, "").strip().lower()
        if forced_tool and self._tool_registry.get(forced_tool) is not None:
            # 插件工具暂时不纳入启发式关键词选择；测试、后台任务或未来模型 planner
            # 可以通过 metadata 显式指定工具，执行阶段仍会走统一权限和审批检查。
            forced_input = metadata.get(METADATA_AGENT_REQUIRED_TOOL_INPUT_KEY, "").strip()
            return PlannerDecision(
                step_index=step.index,
                objective=step.objective,
                tool_name=forced_tool,
                tool_input=forced_input or step.objective,
                planner="metadata_forced",
                reason="metadata_required_tool",
            )

        tool_name, tool_input = self._select_tool(
            step.objective,
            prompt,
            context_url=context_url,
        )
        planner_name = "mock_deterministic" if self.model_provider == "mock" else "heuristic_fallback"
        return PlannerDecision(
            step_index=step.index,
            objective=step.objective,
            tool_name=tool_name,
            tool_input=tool_input,
            planner=planner_name,
            reason="deterministic_rule_match" if tool_name != "none" else "no_matching_tool",
        )

    def _planner_select_tool_with_model(
        self,
        step: PlannedStep,
        prompt: str,
        context_url: str,
        metadata: dict[str, str],
    ) -> PlannerDecision | None:
        # 预留 openai provider 的模型选工具路径。当前不在执行循环里额外发起模型请求，
        # 避免改变延迟和费用；后续可在这里解析模型返回的结构化 ToolCall。
        if self.model_provider != "openai":
            return None

        _ = (step, prompt, context_url, metadata)
        return None

    def _planner_decision_events(self, decision: PlannerDecision) -> tuple[RuntimeStreamItem, ...]:
        if not decision.uses_tool:
            return ()

        return (
            RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="tool_selected",
                    payload=self._build_tool_event_payload(
                        step_index=decision.step_index,
                        objective=decision.objective,
                        tool_name=decision.tool_name,
                        tool_input=decision.tool_input,
                        extra={
                            "planner": decision.planner,
                            "planner_reason": decision.reason,
                        },
                    ),
                    display_message=f"Tool selected: {decision.tool_name}",
                ),
            ),
            RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="decide",
                    payload={
                        "step_index": decision.step_index,
                        "tool": decision.tool_name,
                        "tool_input": decision.tool_input,
                        "planner": decision.planner,
                        "reason": decision.reason,
                    },
                ),
            ),
        )

    async def _executor_run_decision_events(
        self,
        decision: PlannerDecision,
        task_id: str,
        user_id: str,
        user_role: str,
        prompt: str,
        metadata: dict[str, str],
        recalled_memories: list[dict[str, Any]],
        approval_granted: bool,
        approved_tools: set[str],
        approved_tool_call: dict[str, Any] | None,
        outcome_box: dict[str, ToolExecutionOutcome],
    ) -> AsyncIterator[RuntimeStreamItem]:
        # executor 只负责应用策略护栏并调用工具；失败会变成 outcome，
        # 不会向外抛出导致整个 Agent 循环崩溃。
        if not decision.uses_tool:
            outcome = ToolExecutionOutcome(
                status="skipped",
                observation="no external tool required",
                reason="no_tool_selected",
                completed=True,
            )
            outcome_box["outcome"] = outcome
            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="tool_skipped",
                    payload=self._build_tool_event_payload(
                        step_index=decision.step_index,
                        objective=decision.objective,
                        tool_name=decision.tool_name,
                        tool_input=decision.tool_input,
                        reason=outcome.reason,
                    ),
                    display_message="Tool skipped: no external tool required",
                ),
            )
            return

        if not self._is_tool_allowed_for_role(decision.tool_name, user_role):
            observation = f"tool {decision.tool_name} is blocked for role {user_role}"
            self._tool_audit.log(
                task_id=task_id,
                user_id=user_id,
                user_role=user_role,
                tool_name=decision.tool_name,
                tool_input=decision.tool_input,
                outcome=observation,
                ok=False,
                duration_ms=0,
                reason="policy_blocked",
                action="blocked",
                risk_level=self._tool_risk_level(decision.tool_name),
            )
            outcome = ToolExecutionOutcome(
                status="skipped",
                observation=observation,
                reason="policy_blocked",
                blocked=True,
            )
            outcome_box["outcome"] = outcome
            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="policy_blocked",
                    payload={
                        "step_index": decision.step_index,
                        "tool": decision.tool_name,
                        "role": user_role,
                    },
                ),
            )
            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="tool_skipped",
                    payload=self._build_tool_event_payload(
                        step_index=decision.step_index,
                        objective=decision.objective,
                        tool_name=decision.tool_name,
                        tool_input=decision.tool_input,
                        reason=outcome.reason,
                        extra={"role": user_role},
                    ),
                    display_message=f"Tool skipped: {decision.tool_name} blocked for role {user_role}",
                ),
            )
            return

        tool_risk_level = self._tool_risk_level(decision.tool_name)
        is_approved, approval_source = self._is_tool_call_approved(
            decision=decision,
            approved_tools=approved_tools,
            approved_tool_call=approved_tool_call,
        )
        # approval_granted 仍由 Gateway 写入，便于旧链路识别“这是一条恢复任务”，
        # 但真正放行必须匹配 approved_tool_call 或旧的 approved_tools，避免仅靠布尔值放行高风险工具。
        _ = approval_granted
        if self._tool_requires_approval(decision.tool_name) and not is_approved:
            observation = f"tool {decision.tool_name} requires explicit approval"
            self._tool_audit.log(
                task_id=task_id,
                user_id=user_id,
                user_role=user_role,
                tool_name=decision.tool_name,
                tool_input=decision.tool_input,
                outcome=observation,
                ok=False,
                duration_ms=0,
                reason="approval_required",
                action="approval_required",
                risk_level=tool_risk_level,
                details={
                    "resume_step_index": decision.step_index,
                    "approval_record": self._build_approval_record(decision, tool_risk_level),
                },
            )
            outcome = ToolExecutionOutcome(
                status="approval_required",
                observation=observation,
                reason="approval_required",
                blocked=True,
                should_pause=True,
            )
            outcome_box["outcome"] = outcome
            pause_payload = {
                "step_index": decision.step_index,
                "tool": decision.tool_name,
                "tool_name": decision.tool_name,
                "tool_input": decision.tool_input,
                "risk_level": tool_risk_level,
                "resume_step_index": decision.step_index,
                "reason": outcome.reason,
                "approval_reason": f"{tool_risk_level} risk tool call requires approval",
                "approved_tool_call": self._build_approval_record(decision, tool_risk_level),
                "hint": "call task approve endpoint to resume execution",
            }
            pause_payload.update(
                self._build_tool_event_payload(
                    step_index=decision.step_index,
                    objective=decision.objective,
                    tool_name=decision.tool_name,
                    tool_input=decision.tool_input,
                    reason=outcome.reason,
                )
            )
            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="approval_required",
                    payload=pause_payload,
                    display_message=f"Approval required for tool: {decision.tool_name}",
                ),
            )
            yield RuntimeStreamItem(
                kind="pause",
                message=self._encode_agent_info(
                    phase="paused",
                    payload={
                        "reason": observation,
                        "tool": decision.tool_name,
                        "resume_step_index": decision.step_index,
                    },
                ),
            )
            return

        if self._tool_requires_approval(decision.tool_name):
            self._tool_audit.log(
                task_id=task_id,
                user_id=user_id,
                user_role=user_role,
                tool_name=decision.tool_name,
                tool_input=decision.tool_input,
                outcome=f"tool call approved by {approval_source}",
                ok=True,
                duration_ms=0,
                reason="approved",
                action="approved",
                risk_level=tool_risk_level,
                details={
                    "resume_step_index": decision.step_index,
                    "approval_source": approval_source,
                },
            )

        yield RuntimeStreamItem(
            kind="info",
            message=self._encode_agent_info(
                phase="tool_started",
                payload=self._build_tool_event_payload(
                    step_index=decision.step_index,
                    objective=decision.objective,
                    tool_name=decision.tool_name,
                    tool_input=decision.tool_input,
                ),
                display_message=f"Tool started: {decision.tool_name}",
            ),
        )

        tool_started_at = time.time()
        try:
            result = await asyncio.to_thread(
                self._execute_tool,
                task_id,
                user_id,
                user_role,
                decision.tool_name,
                decision.tool_input,
                prompt,
                metadata,
                recalled_memories,
            )
        except Exception as exc:
            result = ToolResult.failure(
                f"{decision.tool_name} failed: {exc}",
                code="executor_exception",
            )

        tool_duration_ms = int((time.time() - tool_started_at) * 1000)
        status = "finished" if result.ok else "failed"
        outcome = ToolExecutionOutcome(
            status=status,
            observation=result.output,
            result=result,
            duration_ms=tool_duration_ms,
            completed=result.ok,
            tool_called=True,
            tool_succeeded=result.ok,
        )
        outcome_box["outcome"] = outcome

        phase = "tool_finished" if result.ok else "tool_failed"
        display = "Tool finished" if result.ok else "Tool failed"
        yield RuntimeStreamItem(
            kind="info",
            message=self._encode_agent_info(
                phase=phase,
                payload=self._build_tool_event_payload(
                    step_index=decision.step_index,
                    objective=decision.objective,
                    tool_name=decision.tool_name,
                    tool_input=decision.tool_input,
                    result=result,
                    duration_ms=tool_duration_ms,
                ),
                display_message=f"{display}: {decision.tool_name}",
            ),
        )

    def _observer_payload(
        self,
        decision: PlannerDecision,
        outcome: ToolExecutionOutcome,
        replanned: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "step_index": decision.step_index,
            "observation": outcome.observation,
            "tool": decision.tool_name,
            "status": outcome.status,
        }
        if outcome.reason:
            payload["reason"] = outcome.reason
        if replanned:
            payload["replanned"] = True
        return payload

    def _observer_reflect_decision(
        self,
        decision: PlannerDecision,
        outcome: ToolExecutionOutcome,
    ) -> str:
        # observer 将执行结果统一转成反思文本，失败也只是普通观察结果。
        return self._reflect_step(decision.objective, outcome.observation)

    def _replanner_after_failure(
        self,
        step: PlannedStep,
        failed_decision: PlannerDecision,
        outcome: ToolExecutionOutcome,
        prompt: str,
        context_url: str,
        already_replanned: bool,
    ) -> ReplanDecision:
        # replanner 只允许每个任务接管一次，避免失败工具反复重试形成循环。
        if already_replanned or outcome.status != "failed":
            return ReplanDecision(should_replan=False)

        if failed_decision.tool_name == "code_exec":
            expression = self._extract_math_expression(failed_decision.tool_input)
            if expression:
                return ReplanDecision(
                    should_replan=True,
                    reason="code_exec_failed_use_calculator",
                    decision=PlannerDecision(
                        step_index=step.index,
                        objective=step.objective,
                        tool_name="calculator",
                        tool_input=expression,
                        planner="replanner",
                        reason="fallback_to_calculator",
                    ),
                )

        if failed_decision.tool_name in {
            "browser_fetch",
            "http_api",
            "search",
            "open_url",
            "extract_text",
            "summarize_page",
        }:
            return ReplanDecision(
                should_replan=True,
                reason="network_tool_failed_use_retrieval",
                decision=PlannerDecision(
                    step_index=step.index,
                    objective=step.objective,
                    tool_name="retrieval",
                    tool_input=step.objective,
                    planner="replanner",
                    reason="fallback_to_retrieval",
                ),
            )

        _ = (prompt, context_url)
        return ReplanDecision(
            should_replan=True,
            reason="tool_failed_continue_without_tool",
            decision=PlannerDecision(
                step_index=step.index,
                objective=step.objective,
                tool_name="none",
                tool_input="",
                planner="replanner",
                reason="continue_without_tool",
            ),
        )

    def _build_plan_steps(self, prompt: str) -> list[PlannedStep]:
        pieces: list[str] = []
        for block in re.split(r"[\n;]+", prompt):
            for piece in re.split(
                r"\bthen\b|\band then\b|\bnext\b|然后|接着|并且|同时|再|最后",
                block,
                flags=re.IGNORECASE,
            ):
                normalized = " ".join(piece.strip().split())
                if not normalized:
                    continue
                pieces.append(normalized)

        if not pieces:
            pieces = [prompt]

        deduplicated: list[str] = []
        seen: set[str] = set()
        for piece in pieces:
            key = piece.lower()
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(piece)
            if len(deduplicated) >= self._agent_max_plan_steps:
                break

        return [
            PlannedStep(index=index + 1, objective=objective)
            for index, objective in enumerate(deduplicated)
        ]

    def _select_tool(
        self,
        objective: str,
        prompt: str,
        context_url: str = "",
    ) -> tuple[str, str]:
        lowered = objective.lower()

        retrieval_intents = (
            "memory",
            "context",
            "previous",
            "recall",
            "conversation history",
            "chat history",
            "检索",
            "上下文",
            "前文",
            "上文",
            "历史记录",
            "聊天记录",
        )

        if any(token in lowered for token in retrieval_intents):
            return ("retrieval", objective)

        # json_echo 主要用于协议冒烟测试；必须出现明确短语，
        # 避免普通提示词误选该工具。
        if "json_echo" in lowered or "json echo" in lowered:
            return ("json_echo", objective)

        url = self._extract_first_url(objective) or self._extract_first_url(prompt)
        if url:
            normalized_url = url.lower()
            if any(token in lowered for token in ("search", "lookup", "web search", "网页搜索", "搜索网页")):
                return ("search", objective)
            if "api" in lowered or "/api/" in normalized_url or normalized_url.endswith(".json"):
                return ("http_api", url)
            if "browser_fetch" in lowered:
                return ("browser_fetch", url)
            if any(token in lowered for token in ("extract", "text", "正文", "提取")):
                return ("extract_text", url)
            if any(token in lowered for token in ("summary", "summarize", "总结", "摘要", "概括")):
                return ("summarize_page", url)
            if any(token in lowered for token in ("cite", "citation", "source citation", "来源", "引用")):
                return ("source_citation", url)
            if any(token in lowered for token in ("open", "visit", "read", "打开", "访问", "浏览")):
                return ("open_url", url)
            return ("open_url", url)

        if any(token in lowered for token in ("python", "script", "code", "代码", "脚本", "执行")):
            candidate_expression = self._extract_math_expression(objective) or objective
            return ("code_exec", candidate_expression)

        math_expression = self._extract_math_expression(objective)
        if math_expression:
            return ("calculator", math_expression)

        if any(token in lowered for token in ("search", "lookup", "web search", "网页搜索", "搜索网页")):
            return ("search", objective)

        if any(token in lowered for token in ("search", "lookup", "retrieve", "查询", "搜", "检索")):
            return ("retrieval", objective)

        if context_url and self._is_web_followup_intent(objective):
            normalized_url = context_url.lower()
            if "api" in lowered or "/api/" in normalized_url or normalized_url.endswith(".json"):
                return ("http_api", context_url)
            if any(token in lowered for token in ("extract", "text", "正文", "提取")):
                return ("extract_text", context_url)
            if any(token in lowered for token in ("summary", "summarize", "总结", "摘要", "概括")):
                return ("summarize_page", context_url)
            if any(token in lowered for token in ("cite", "citation", "source citation", "来源", "引用")):
                return ("source_citation", context_url)
            return ("open_url", context_url)

        return ("none", "")

    def _extract_first_url(self, text: str) -> str:
        match = re.search(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", text)
        if not match:
            return ""

        return match.group(0).rstrip(".,;:!?\"'，。；：！？、）】》")

    def _extract_latest_context_url(self, prompt: str, metadata: dict[str, str]) -> str:
        prompt_url = self._extract_first_url(prompt)
        if prompt_url:
            return prompt_url

        raw_messages = metadata.get(MODEL_MESSAGES_METADATA_KEY, "").strip()
        if raw_messages:
            try:
                parsed = json.loads(raw_messages)
            except json.JSONDecodeError:
                parsed = []

            if isinstance(parsed, list):
                for item in reversed(parsed):
                    if not isinstance(item, dict):
                        continue
                    content = item.get("content")
                    if not isinstance(content, str):
                        continue
                    matched_url = self._extract_first_url(content)
                    if matched_url:
                        return matched_url

        return self._extract_first_url(metadata.get(METADATA_CLIENT_USER_MESSAGE_KEY, ""))

    def _is_web_followup_intent(self, objective: str) -> bool:
        lowered = objective.lower()
        followup_tokens = (
            "网页",
            "页面",
            "网站",
            "链接",
            "网址",
            "论文",
            "内容",
            "详情",
            "总结",
            "讲了什么",
            "web page",
            "website",
            "page",
            "link",
            "url",
            "paper",
            "content",
            "details",
            "summary",
            "what does it say",
        )
        return any(token in lowered for token in followup_tokens)

    def _extract_math_expression(self, text: str) -> str:
        candidates = re.findall(r"[0-9\s\+\-\*\/%\(\)\.]{3,}", text)
        for raw in candidates:
            normalized = " ".join(raw.split())
            if not normalized:
                continue
            if not any(ch.isdigit() for ch in normalized):
                continue
            if not any(op in normalized for op in "+-*/%"):
                continue
            return normalized

        return ""

    def _is_tool_allowed_for_role(self, tool_name: str, role: str) -> bool:
        return self._tool_policy.is_tool_allowed(role, tool_name)

    def _tool_requires_approval(self, tool_name: str) -> bool:
        return self._tool_policy.requires_approval(tool_name)

    def _tool_risk_level(self, tool_name: str) -> str:
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return ""
        return str(getattr(tool, "risk_level", "") or "")

    def _build_approval_record(
        self,
        decision: PlannerDecision,
        risk_level: str,
    ) -> dict[str, Any]:
        # 审批记录绑定到一次具体 tool call，而不是只绑定工具名，避免恢复时放行同名不同参数的调用。
        return {
            "tool_name": decision.tool_name,
            "tool_input": decision.tool_input,
            "risk_level": risk_level,
            "reason": f"{risk_level} risk tool call requires approval",
            "resume_step_index": decision.step_index,
        }

    def _parse_approved_tool_call(self, metadata: dict[str, str]) -> dict[str, Any] | None:
        # Gateway 将审批记录作为 JSON 字符串写入 metadata；runtime 在入口处解析一次，
        # 执行阶段只做结构化字段比对，避免每个工具重复处理元数据格式。
        raw = metadata.get(METADATA_APPROVED_TOOL_CALL_KEY, "").strip()
        if not raw:
            return None

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(decoded, dict):
            return None

        tool_name = str(decoded.get("tool_name", decoded.get("tool", ""))).strip().lower()
        tool_input = str(decoded.get("tool_input", "")).strip()
        risk_level = str(decoded.get("risk_level", "")).strip().lower()
        reason = str(decoded.get("reason", "")).strip()
        resume_step_index = self._read_int(str(decoded.get("resume_step_index", "")), 0)
        if not tool_name:
            return None

        return {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "risk_level": risk_level,
            "reason": reason,
            "resume_step_index": resume_step_index,
        }

    def _is_tool_call_approved(
        self,
        decision: PlannerDecision,
        approved_tools: set[str],
        approved_tool_call: dict[str, Any] | None,
    ) -> tuple[bool, str]:
        # approved_tools 保留旧的工具名级别审批；approved_tool_call 是新的精确审批路径。
        if decision.tool_name in approved_tools:
            return True, "approved_tools"
        if approved_tool_call is None:
            return False, ""

        if str(approved_tool_call.get("tool_name", "")).strip().lower() != decision.tool_name:
            return False, ""
        if self._normalize_approval_text(
            str(approved_tool_call.get("tool_input", ""))
        ) != self._normalize_approval_text(decision.tool_input):
            return False, ""

        approved_step = int(approved_tool_call.get("resume_step_index", 0) or 0)
        if approved_step > 0 and approved_step != decision.step_index:
            return False, ""

        approved_risk = str(approved_tool_call.get("risk_level", "")).strip().lower()
        current_risk = self._tool_risk_level(decision.tool_name).strip().lower()
        if approved_risk and current_risk and approved_risk != current_risk:
            return False, ""

        return True, "approved_tool_call"

    def _normalize_approval_text(self, value: str) -> str:
        return " ".join(value.strip().split())

    def _execute_tool(
        self,
        task_id: str,
        user_id: str,
        user_role: str,
        tool_name: str,
        tool_input: str,
        prompt: str,
        metadata: dict[str, str],
        recalled_memories: list[dict[str, Any]],
    ) -> ToolResult:
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            result = ToolResult(ok=False, output=f"unsupported tool: {tool_name}")
            self._tool_audit.log(
                task_id=task_id,
                user_id=user_id,
                user_role=user_role,
                tool_name=tool_name,
                tool_input=tool_input,
                outcome=result.output,
                ok=False,
                duration_ms=0,
                reason="unregistered_tool",
                action="failed",
                risk_level=self._tool_risk_level(tool_name),
            )
            return result

        context = ToolContext(
            task_id=task_id,
            user_id=user_id,
            user_role=user_role,
            prompt=prompt,
            metadata=metadata,
            recalled_memories=recalled_memories,
        )

        started_at = time.time()
        # planner 仍返回 (tool_name, plain_text_input)。在 runtime 边界把
        # 旧形态转换为标准 ToolCall 信封，让具体工具只实现新协议。
        call = ToolCall(
            tool_name=tool_name,
            input_text=tool_input,
            arguments=self._build_tool_call_arguments(tool_name, tool_input),
        )
        result = tool.execute(call, context)
        duration_ms = int((time.time() - started_at) * 1000)

        self._tool_audit.log(
            task_id=task_id,
            user_id=user_id,
            user_role=user_role,
            tool_name=tool_name,
            tool_input=tool_input,
            outcome=result.output,
            ok=result.ok,
            duration_ms=duration_ms,
            reason=result.error.code if result.error is not None else "",
            action="executed" if result.ok else "failed",
            risk_level=self._tool_risk_level(tool_name),
        )

        return result

    def _build_tool_call_arguments(self, tool_name: str, tool_input: str) -> dict[str, Any]:
        # 该适配器保留旧 selector 契约，同时为每个内置工具提供其
        # input_schema 声明的结构化参数键。
        normalized_name = tool_name.strip().lower()
        if normalized_name == "calculator":
            return {"expression": tool_input}
        if normalized_name in {"browser_fetch", "http_api", "open_url", "extract_text", "summarize_page"}:
            return {"url": tool_input}
        if normalized_name == "search":
            return {"query": tool_input}
        if normalized_name == "source_citation":
            return {"url": tool_input}
        if normalized_name == "code_exec":
            return {"code": tool_input}
        if normalized_name == "json_echo":
            return {"payload": tool_input}
        if normalized_name == "retrieval":
            return {"query": tool_input}

        raw_input = tool_input.strip()
        if raw_input.startswith("{"):
            try:
                decoded = json.loads(raw_input)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                return decoded

        tool = self._tool_registry.get(normalized_name)
        if tool is not None:
            properties = tool.input_schema.get("properties")
            if isinstance(properties, dict) and len(properties) == 1:
                # 插件工具常见的是单字段 object schema；这里把 planner 的纯文本输入
                # 自动映射到唯一字段，避免每个 provider 都实现一层旧输入兼容逻辑。
                only_key = next(iter(properties.keys()))
                if isinstance(only_key, str) and only_key:
                    return {only_key: raw_input}
        return {"input": tool_input}

    def _execute_http_tool(
        self,
        url: str,
        parse_json: bool,
    ) -> ToolResult:
        tool_name = "http_api" if parse_json else "browser_fetch"
        document_result = self._fetch_browser_document(tool_name, url)
        if isinstance(document_result, ToolResult):
            return document_result

        document = document_result
        if parse_json:
            payload = document.raw_text
            try:
                parsed_json = json.loads(document.raw_text)
                payload = json.dumps(parsed_json, ensure_ascii=True)
            except json.JSONDecodeError:
                payload = document.raw_text
            return ToolResult.success(
                f"{tool_name} response: {payload[:BROWSER_OUTPUT_TEXT_LIMIT]}",
                metadata=self._browser_document_metadata(tool_name, document),
            )

        return ToolResult.success(
            f"{tool_name} response: Source: {document.final_url} | {document.text[:BROWSER_OUTPUT_TEXT_LIMIT]}",
            metadata=self._browser_document_metadata(tool_name, document),
        )

    def _execute_browser_tool(
        self,
        operation: str,
        call: ToolCall,
        context: ToolContext,
    ) -> ToolResult:
        # 浏览工具统一在 runtime 执行，便于把网络安全策略、审计和错误分类集中到一个边界。
        # context 当前只用于保留接口一致性，后续可用于按用户或任务追加浏览策略。
        _ = context
        normalized_operation = operation.strip().lower()

        if normalized_operation == "search":
            return self._execute_browser_search(call.argument_text("query"))
        if normalized_operation == "source_citation":
            return self._execute_source_citation(
                url=call.argument_text("url"),
                title=str(call.arguments.get("title", "") or ""),
                snippet=str(call.arguments.get("snippet", "") or ""),
            )

        url = call.argument_text("url")
        document_result = self._fetch_browser_document(normalized_operation, url)
        if isinstance(document_result, ToolResult):
            return document_result

        document = document_result
        if normalized_operation == "open_url":
            title = document.title or "(untitled)"
            output = (
                f"open_url result: Source: {document.final_url} | "
                f"Status: {document.status_code} | Content-Type: {document.content_type} | Title: {title}"
            )
            return ToolResult.success(
                output,
                metadata=self._browser_document_metadata(normalized_operation, document),
            )

        if normalized_operation == "extract_text":
            output = (
                f"extract_text result: Source: {document.final_url}\n"
                f"Title: {document.title or '(untitled)'}\n"
                f"Text: {document.text[:BROWSER_OUTPUT_TEXT_LIMIT]}"
            )
            return ToolResult.success(
                output,
                metadata=self._browser_document_metadata(normalized_operation, document),
            )

        if normalized_operation == "summarize_page":
            points = self._summarize_fallback_points(document.text)
            if not points:
                points = [document.title or "No readable text extracted from page."]
            lines = ["summarize_page result:", f"Source: {document.final_url}"]
            for point in points[:4]:
                lines.append(f"- {point}")
            lines.append(f"Sources: [1] {document.final_url}")
            return ToolResult.success(
                "\n".join(lines),
                metadata=self._browser_document_metadata(normalized_operation, document),
            )

        return ToolResult.failure(
            f"{normalized_operation} failed: unsupported browser operation",
            code="unsupported_browser_operation",
            details={"operation": normalized_operation},
        )

    def _execute_browser_search(self, query: str) -> ToolResult:
        normalized_query = " ".join(query.strip().split())
        if not normalized_query:
            return ToolResult.failure(
                "search failed: query is required",
                code="invalid_input",
            )

        urls = self._extract_urls(normalized_query)
        allowed_urls: list[str] = []
        blocked_urls: list[str] = []
        for raw_url in urls:
            normalized_url = self._normalize_tool_url(raw_url)
            parsed = urllib_parse.urlparse(normalized_url)
            host = (parsed.hostname or "").strip().lower()
            if parsed.scheme in {"http", "https"} and parsed.netloc and self._is_host_allowed(host):
                allowed_urls.append(normalized_url)
            else:
                blocked_urls.append(normalized_url or raw_url)

        if allowed_urls:
            lines = ["search results:"]
            for index, source_url in enumerate(allowed_urls[:5], start=1):
                lines.append(f"[{index}] {source_url} - candidate source from query")
            return ToolResult.success(
                "\n".join(lines),
                metadata={
                    "operation": "search",
                    "sources": allowed_urls[:5],
                    "blocked_sources": blocked_urls[:5],
                    "audit": {
                        "allowlist_checked": True,
                        "network_request": False,
                    },
                },
            )

        return ToolResult.failure(
            "search failed: no allowlisted URL found in query and no search provider is configured",
            code="search_provider_unavailable",
            retryable=False,
            details={
                "operation": "search",
                "query_preview": normalized_query[:160],
                "blocked_sources": blocked_urls[:5],
            },
        )

    def _execute_source_citation(self, url: str, title: str = "", snippet: str = "") -> ToolResult:
        normalized_url = self._normalize_tool_url(url)
        validation_error = self._validate_browser_url("source_citation", normalized_url)
        if validation_error is not None:
            return validation_error

        safe_title = " ".join(title.strip().split()) or normalized_url
        safe_snippet = " ".join(snippet.strip().split())
        citation = f"source_citation result: [1] {safe_title} - {normalized_url}"
        if safe_snippet:
            citation += f" | {safe_snippet[:220]}"

        return ToolResult.success(
            citation,
            metadata={
                "operation": "source_citation",
                "source_url": normalized_url,
                "sources": [normalized_url],
                "audit": {
                    "allowlist_checked": True,
                    "network_request": False,
                },
            },
        )

    def _fetch_browser_document(self, operation: str, url: str) -> BrowserDocument | ToolResult:
        normalized_url = self._normalize_tool_url(url)
        validation_error = self._validate_browser_url(operation, normalized_url)
        if validation_error is not None:
            return validation_error

        request = urllib_request.Request(
            normalized_url,
            headers={
                "Accept": "text/html, application/json, text/plain, */*",
                "User-Agent": BROWSER_USER_AGENT,
            },
            method="GET",
        )

        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                with urllib_request.urlopen(request, timeout=self._agent_tool_http_timeout_seconds) as response:
                    raw_bytes = response.read(BROWSER_MAX_CONTENT_BYTES + 1)
                    truncated = len(raw_bytes) > BROWSER_MAX_CONTENT_BYTES
                    if truncated:
                        raw_bytes = raw_bytes[:BROWSER_MAX_CONTENT_BYTES]

                    charset = ""
                    try:
                        charset = response.headers.get_content_charset() or ""
                    except Exception:
                        charset = ""
                    raw_text = raw_bytes.decode(charset or "utf-8", errors="replace")
                    content_type = response.headers.get("Content-Type", "") if response.headers else ""
                    final_url = response.geturl() or normalized_url
                    text = self._extract_browser_text(raw_text, content_type)
                    title = self._extract_browser_title(raw_text)
                    return BrowserDocument(
                        requested_url=normalized_url,
                        final_url=final_url,
                        status_code=int(getattr(response, "status", response.getcode()) or 0),
                        content_type=content_type,
                        raw_text=raw_text,
                        text=text,
                        title=title,
                        byte_count=len(raw_bytes),
                        truncated=truncated,
                    )
            except urllib_error.HTTPError as exc:
                body = exc.read(BROWSER_OUTPUT_TEXT_LIMIT).decode("utf-8", errors="replace")
                retryable = exc.code in {408, 429, 500, 502, 503, 504}
                if attempt < attempts and retryable:
                    time.sleep(0.35 * attempt)
                    continue
                return self._browser_failure(
                    operation,
                    f"HTTP {exc.code} {body[:240]}",
                    code="http_error",
                    retryable=retryable,
                    details={
                        "url": normalized_url,
                        "status_code": exc.code,
                    },
                )
            except urllib_error.URLError as exc:
                failure = self._browser_failure_from_url_error(operation, normalized_url, exc)
                if attempt < attempts and failure.error is not None and failure.error.retryable:
                    time.sleep(0.35 * attempt)
                    continue
                return failure
            except (TimeoutError, socket.timeout) as exc:
                if attempt < attempts:
                    time.sleep(0.35 * attempt)
                    continue
                return self._browser_failure(
                    operation,
                    f"request timed out after {self._agent_tool_http_timeout_seconds:.1f}s",
                    code="timeout",
                    retryable=True,
                    details={"url": normalized_url, "exception": str(exc)},
                )
            except Exception as exc:
                return self._browser_failure(
                    operation,
                    str(exc),
                    code="request_failed",
                    retryable=False,
                    details={"url": normalized_url},
                )

        return self._browser_failure(
            operation,
            "request failed after retries",
            code="request_failed",
            retryable=True,
            details={"url": normalized_url},
        )

    def _validate_browser_url(self, operation: str, url: str) -> ToolResult | None:
        if not url:
            return self._browser_failure(operation, "URL is required", code="invalid_url")

        parsed = urllib_parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return self._browser_failure(
                operation,
                "only HTTP and HTTPS URLs are supported",
                code="unsupported_url",
                details={"url": url},
            )

        host = (parsed.hostname or "").strip().lower()
        if not self._is_host_allowed(host):
            return self._browser_failure(
                operation,
                f"host {host} is not in allowlist",
                code="host_not_allowed",
                details={"url": url, "host": host},
            )

        return None

    def _browser_failure_from_url_error(
        self,
        operation: str,
        url: str,
        exc: urllib_error.URLError,
    ) -> ToolResult:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return self._browser_failure(
                operation,
                f"request timed out after {self._agent_tool_http_timeout_seconds:.1f}s",
                code="timeout",
                retryable=True,
                details={"url": url},
            )

        return self._browser_failure(
            operation,
            str(reason),
            code="network_error",
            retryable=True,
            details={"url": url},
        )

    def _browser_failure(
        self,
        operation: str,
        message: str,
        code: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> ToolResult:
        failure_details = dict(details or {})
        failure_details.setdefault("operation", operation)
        failure_details.setdefault("timeout_seconds", self._agent_tool_http_timeout_seconds)
        failure_details.setdefault("max_content_bytes", BROWSER_MAX_CONTENT_BYTES)
        return ToolResult.failure(
            f"{operation} failed: {message}",
            code=code,
            retryable=retryable,
            details=failure_details,
        )

    def _browser_document_metadata(
        self,
        operation: str,
        document: BrowserDocument,
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "source_url": document.final_url,
            "sources": [document.final_url],
            "requested_url": document.requested_url,
            "status_code": document.status_code,
            "content_type": document.content_type,
            "byte_count": document.byte_count,
            "truncated": document.truncated,
            "title": document.title,
            "audit": {
                "allowlist_checked": True,
                "timeout_seconds": self._agent_tool_http_timeout_seconds,
                "max_content_bytes": BROWSER_MAX_CONTENT_BYTES,
                "network_request": True,
            },
        }

    def _extract_browser_title(self, raw_text: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", raw_text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""

        title = re.sub(r"<[^>]+>", " ", match.group(1))
        return " ".join(html.unescape(title).split())[:220]

    def _extract_browser_text(self, raw_text: str, content_type: str) -> str:
        if "json" in content_type.lower():
            try:
                parsed = json.loads(raw_text)
                return json.dumps(parsed, ensure_ascii=True)[:BROWSER_OUTPUT_TEXT_LIMIT]
            except json.JSONDecodeError:
                pass

        cleaned = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw_text)
        cleaned = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", cleaned)
        cleaned = re.sub(r"(?is)<!--.*?-->", " ", cleaned)
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        return " ".join(html.unescape(cleaned).split())

    def _extract_urls(self, text: str) -> list[str]:
        matches = re.findall(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", text)
        urls: list[str] = []
        seen: set[str] = set()
        for raw_url in matches:
            normalized = self._normalize_tool_url(raw_url)
            lowered = normalized.lower()
            if not normalized or lowered in seen:
                continue
            seen.add(lowered)
            urls.append(normalized)
        return urls

    def _normalize_tool_url(self, text: str) -> str:
        candidate = self._extract_first_url(text)
        if not candidate:
            candidate = text.strip()

        candidate = candidate.strip().strip('"\'<>')
        candidate = candidate.rstrip(".,;:!?，。；：！？、）】》")
        return candidate

    def _build_tool_observation_fallback(
        self,
        prompt: str,
        tool_observations: list[tuple[str, str]],
        synthesis_error: str,
    ) -> str:
        if not tool_observations:
            return ""

        for tool_name, raw_output in reversed(tool_observations):
            if tool_name not in {
                "browser_fetch",
                "http_api",
                "search",
                "open_url",
                "extract_text",
                "summarize_page",
                "source_citation",
            }:
                continue

            payload = self._extract_tool_payload(raw_output, tool_name)
            points = self._summarize_fallback_points(payload)
            if not points:
                continue

            use_chinese = self._contains_chinese(prompt)
            quota_limited = self._is_quota_error(synthesis_error)
            if use_chinese:
                if quota_limited:
                    header = "网页已成功抓取，但模型服务触发配额或限流。先给你基于抓取结果的摘要："
                else:
                    header = "网页已成功抓取，但模型服务暂时不可用。先给你基于抓取结果的摘要："
            else:
                if quota_limited:
                    header = "Page fetch succeeded, but model provider quota/rate limit was hit. Here is a direct summary from fetched content:"
                else:
                    header = "Page fetch succeeded, but model service is temporarily unavailable. Here is a direct summary from fetched content:"

            lines = [header]
            for point in points[:4]:
                lines.append(f"- {point}")
            return "\n".join(lines)

        return ""

    def _extract_tool_payload(self, tool_output: str, tool_name: str) -> str:
        normalized = " ".join(tool_output.strip().split())
        if not normalized:
            return ""

        prefix = f"{tool_name} response:"
        lowered = normalized.lower()
        if lowered.startswith(prefix):
            return normalized[len(prefix) :].strip()
        result_prefix = f"{tool_name} result:"
        if lowered.startswith(result_prefix):
            return normalized[len(result_prefix) :].strip()
        if tool_name == "search" and lowered.startswith("search results:"):
            return normalized[len("search results:") :].strip()
        return normalized

    def _summarize_fallback_points(self, payload: str) -> list[str]:
        normalized = " ".join(payload.strip().split())
        if not normalized:
            return []

        collected: list[str] = []
        seen: set[str] = set()

        def append_point(value: str) -> None:
            candidate = " ".join(value.strip().split())
            if len(candidate) < 8:
                return
            lowered = candidate.lower()
            if lowered in seen:
                return
            seen.add(lowered)
            collected.append(candidate[:180])

        if normalized.startswith("{") or normalized.startswith("["):
            try:
                parsed = json.loads(normalized)
            except json.JSONDecodeError:
                parsed = None

            if parsed is not None:
                for entry in self._extract_priority_json_values(parsed):
                    append_point(entry)
                    if len(collected) >= 4:
                        return collected

        for segment in re.split(r"[。！？.!?;；]+", normalized):
            append_point(segment)
            if len(collected) >= 4:
                return collected

        if not collected:
            append_point(normalized)

        return collected

    def _extract_priority_json_values(self, payload: Any) -> list[str]:
        priority_keys = (
            "title",
            "name",
            "headline",
            "description",
            "summary",
            "abstract",
            "keywords",
            "author",
        )

        values: list[str] = []

        def push(text: str) -> None:
            candidate = " ".join(text.strip().split())
            if not candidate:
                return
            values.append(candidate[:220])

        def walk(node: Any, depth: int) -> None:
            if depth > 4 or len(values) >= 12:
                return

            if isinstance(node, dict):
                for key in priority_keys:
                    if key not in node:
                        continue

                    value = node[key]
                    if isinstance(value, str):
                        push(f"{key}: {value}")
                    elif isinstance(value, (int, float)):
                        push(f"{key}: {value}")
                    elif isinstance(value, dict):
                        nested_name = value.get("name")
                        if isinstance(nested_name, str):
                            push(f"{key}: {nested_name}")

                for value in node.values():
                    walk(value, depth + 1)
                return

            if isinstance(node, list):
                for item in node[:8]:
                    walk(item, depth + 1)

        walk(payload, 0)
        return values

    def _is_host_allowed(self, host: str) -> bool:
        if not host:
            return False
        if not self._agent_tool_http_allowlist:
            return True

        for allowed in self._agent_tool_http_allowlist:
            if host == allowed or host.endswith("." + allowed):
                return True

        return False

    def _safe_eval_expression(self, expression: str) -> str:
        parsed = ast.parse(expression, mode="eval")

        def evaluate(node: ast.AST) -> float:
            if isinstance(node, ast.Expression):
                return evaluate(node.body)

            if isinstance(node, ast.Constant):
                if isinstance(node.value, (int, float)):
                    return float(node.value)
                raise ValueError("unsupported constant")

            if isinstance(node, ast.Num):
                return float(node.n)

            if isinstance(node, ast.UnaryOp):
                operand = evaluate(node.operand)
                if isinstance(node.op, ast.UAdd):
                    return operand
                if isinstance(node.op, ast.USub):
                    return -operand
                raise ValueError("unsupported unary operator")

            if isinstance(node, ast.BinOp):
                left = evaluate(node.left)
                right = evaluate(node.right)

                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Div):
                    return left / right
                if isinstance(node.op, ast.FloorDiv):
                    return left // right
                if isinstance(node.op, ast.Mod):
                    return left % right
                if isinstance(node.op, ast.Pow):
                    if abs(right) > 10:
                        raise ValueError("power exponent too large")
                    return left**right

                raise ValueError("unsupported binary operator")

            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                function_name = node.func.id
                arguments = [evaluate(argument) for argument in node.args]
                if function_name == "abs" and len(arguments) == 1:
                    return abs(arguments[0])
                if function_name == "round" and len(arguments) in {1, 2}:
                    if len(arguments) == 1:
                        return float(round(arguments[0]))
                    return float(round(arguments[0], int(arguments[1])))
                raise ValueError("unsupported function")

            raise ValueError("unsupported expression")

        value = evaluate(parsed)
        if abs(value) > 1e15:
            raise ValueError("result out of allowed range")

        rounded = round(value)
        if abs(value - rounded) < 1e-9:
            return str(int(rounded))
        return str(round(value, 8))

    def _reflect_step(self, objective: str, observation: str) -> str:
        normalized_objective = " ".join(objective.strip().split())
        normalized_observation = " ".join(observation.strip().split())
        if not normalized_observation:
            normalized_observation = "no observation"

        return f"step '{normalized_objective}' => {normalized_observation[:180]}"

    def _evaluate_task(
        self,
        total_steps: int,
        completed_steps: int,
        tool_call_count: int,
        tool_success_count: int,
        blocked_actions: int,
    ) -> AgentEvaluation:
        normalized_total_steps = max(1, total_steps)
        objective_completion = max(0.0, min(1.0, completed_steps / normalized_total_steps))

        if tool_call_count <= 0:
            tool_success_rate = 1.0
        else:
            tool_success_rate = max(0.0, min(1.0, tool_success_count / tool_call_count))

        blocked_penalty = min(0.45, blocked_actions * 0.12)
        estimated_success = (
            0.55 * objective_completion
            + 0.35 * tool_success_rate
            + 0.10
            - blocked_penalty
        )

        estimated_success = max(0.0, min(1.0, estimated_success))
        return AgentEvaluation(
            estimated_success=round(estimated_success, 3),
            objective_completion=round(objective_completion, 3),
            tool_success_rate=round(tool_success_rate, 3),
            blocked_actions=blocked_actions,
        )

    def _build_final_response(
        self,
        prompt: str,
        step_summaries: list[str],
        evaluation: AgentEvaluation,
    ) -> str:
        return self._build_diagnostic_fallback(prompt, step_summaries, evaluation)

    def _build_user_facing_prompt(
        self,
        prompt: str,
        short_context: list[str],
        recalled_memories: list[dict[str, Any]],
        step_summaries: list[str],
        evaluation: AgentEvaluation,
    ) -> str:
        lines = [
            "You are Synapse assistant.",
            "Return a direct, practical final answer for the user request.",
            "Do not output internal labels like Plan/Act/Observe/Reflect unless user explicitly asks.",
            "Use the same language as the user's request.",
            "",
            "User request:",
            prompt,
        ]

        if short_context:
            lines.extend(["", "Recent conversation context:"])
            for item in short_context[-6:]:
                lines.append(f"- {item}")

        if recalled_memories:
            lines.extend(["", "Relevant long-term memory:"])
            for item in recalled_memories[:3]:
                summary = str(item.get("summary", "")).strip()
                if not summary:
                    summary = str(item.get("final_response_preview", "")).strip()
                if not summary:
                    summary = str(item.get("content", "")).strip()
                if summary:
                    lines.append(f"- {summary[:220]}")

        if step_summaries:
            lines.extend(["", "Execution observations:"])
            for summary in step_summaries[-6:]:
                lines.append(f"- {summary}")

        lines.extend(
            [
                "",
                "Execution quality signals:",
                f"- estimated_success={evaluation.estimated_success:.2f}",
                f"- objective_completion={evaluation.objective_completion:.2f}",
                f"- tool_success_rate={evaluation.tool_success_rate:.2f}",
                "",
                "Now provide the final user-facing answer only.",
            ]
        )

        return "\n".join(lines)

    def _build_mock_user_facing_answer(
        self,
        prompt: str,
        step_summaries: list[str],
        evaluation: AgentEvaluation,
    ) -> str:
        lines = [
            "Mock assistant answer",
            f"Request: {prompt}",
        ]

        if step_summaries:
            lines.append("\nExecution notes:")
            for summary in step_summaries[-3:]:
                lines.append(f"- {summary}")

        lines.append("\nThis is a mock-mode response. Configure OpenAI-compatible provider for full-quality content.")
        lines.append(
            f"Current estimated success: {evaluation.estimated_success:.2f}"
        )
        return "\n".join(lines)

    def _build_diagnostic_fallback(
        self,
        prompt: str,
        step_summaries: list[str],
        evaluation: AgentEvaluation,
    ) -> str:
        lines = [
            "Task execution summary (fallback)",
            f"Objective: {prompt}",
            "",
            "Plan-Act-Observe-Reflect trace:",
        ]

        if step_summaries:
            for index, summary in enumerate(step_summaries, start=1):
                lines.append(f"{index}. {summary}")
        else:
            lines.append("1. No explicit execution steps were produced.")

        lines.extend(
            [
                "",
                f"Estimated task success: {evaluation.estimated_success:.2f}",
                f"Objective completion: {evaluation.objective_completion:.2f}",
                f"Tool success rate: {evaluation.tool_success_rate:.2f}",
            ]
        )

        if evaluation.blocked_actions > 0:
            lines.append(
                f"Blocked actions: {evaluation.blocked_actions} (approval or permission policy)."
            )

        return "\n".join(lines)

    def _build_model_unavailable_response(self, error_message: str) -> str:
        if self._contains_chinese(error_message):
            if self._is_quota_error(error_message):
                return "模型服务触发配额或限流，请稍后重试。"
            return "模型服务暂时不可用，请稍后重试。"
        if self._is_quota_error(error_message):
            return "Model provider quota or rate limit reached. Please retry later."
        return "Model service is temporarily unavailable. Please retry shortly."

    def _is_quota_error(self, error_message: str) -> bool:
        lowered = error_message.lower()
        quota_tokens = (
            "http 429",
            "quota",
            "rate limit",
            "resource_exhausted",
            "exceeded your current quota",
        )
        return any(token in lowered for token in quota_tokens)

    def _contains_chinese(self, text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    async def _run_mock(self, prompt: str) -> AsyncIterator[str]:
        # Mock 模式按词流式输出，便于联调与集成测试。
        response = self._build_response(prompt)
        for chunk in self._chunk_text(response):
            await asyncio.sleep(0.04)
            yield chunk

    async def _run_openai(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> AsyncIterator[str]:
        normalized_prompt = " ".join(prompt.strip().split())
        if not normalized_prompt:
            normalized_prompt = "empty request"

        accumulated = ""
        round_index = 0
        current_prompt = normalized_prompt
        long_form_request = self._is_long_form_request(normalized_prompt, metadata)
        marker_buffer = OpenAIDoneMarkerBuffer(OPENAI_DONE_MARKER) if long_form_request else None
        interrupted_round_count = 0
        current_metadata = self._build_openai_generation_metadata(
            normalized_prompt,
            metadata,
            long_form_request=long_form_request,
        )

        while True:
            round_chunks: list[str] = []
            finish_reason = ""
            emitted_this_round = False

            try:
                async for item in self._request_openai_stream_async(
                    current_prompt, current_metadata
                ):
                    if item.finish_reason:
                        finish_reason = item.finish_reason
                    if not item.content:
                        continue

                    emitted_this_round = True
                    if round_index == 0:
                        for visible_chunk in self._filter_openai_visible_chunks(
                            marker_buffer,
                            item.content,
                        ):
                            accumulated += visible_chunk
                            yield visible_chunk
                    else:
                        round_chunks.append(item.content)
            except Exception:
                if emitted_this_round or accumulated:
                    if long_form_request and round_index < self._openai_continuation_max_rounds:
                        finish_reason = "stream_error"
                    else:
                        for visible_chunk in self._flush_openai_visible_chunks(marker_buffer):
                            accumulated += visible_chunk
                            yield visible_chunk
                        raise

                if finish_reason == "stream_error":
                    pass
                else:
                    result = await asyncio.to_thread(
                        self._request_openai_completion_result,
                        current_prompt,
                        current_metadata,
                    )
                    finish_reason = result.finish_reason
                    if not result.content:
                        yield "(empty response)"
                        return

                    if round_index == 0:
                        for chunk in self._chunk_text(result.content):
                            for visible_chunk in self._filter_openai_visible_chunks(
                                marker_buffer,
                                chunk,
                            ):
                                accumulated += visible_chunk
                                yield visible_chunk
                    else:
                        round_chunks.append(result.content)

            for visible_chunk in self._flush_openai_visible_chunks(marker_buffer):
                accumulated += visible_chunk
                yield visible_chunk

            if marker_buffer is not None and marker_buffer.done:
                if self._is_long_form_completion_acceptable(accumulated, normalized_prompt):
                    return
                if round_index >= self._openai_continuation_max_rounds:
                    return
                marker_buffer = OpenAIDoneMarkerBuffer(OPENAI_DONE_MARKER)
                finish_reason = "premature_done_marker"

            if round_index > 0 and round_chunks:
                round_text = "".join(round_chunks)
                continuation_text = self._trim_continuation_overlap(
                    accumulated, round_text
                )
                if self._is_interrupted_openai_finish_reason(finish_reason):
                    continuation_text = self._trim_incomplete_openai_fragment(
                        continuation_text
                    )
                if continuation_text:
                    for chunk in self._chunk_text(continuation_text):
                        for visible_chunk in self._filter_openai_visible_chunks(
                            marker_buffer,
                            chunk,
                        ):
                            accumulated += visible_chunk
                            yield visible_chunk

            for visible_chunk in self._flush_openai_visible_chunks(marker_buffer):
                accumulated += visible_chunk
                yield visible_chunk

            if marker_buffer is not None and marker_buffer.done:
                if self._is_long_form_completion_acceptable(accumulated, normalized_prompt):
                    return
                if round_index >= self._openai_continuation_max_rounds:
                    return
                marker_buffer = OpenAIDoneMarkerBuffer(OPENAI_DONE_MARKER)
                finish_reason = "premature_done_marker"

            if not self._should_continue_openai_response(
                accumulated,
                finish_reason,
                round_index,
                long_form_request=long_form_request,
                done_marker_seen=marker_buffer.done if marker_buffer is not None else False,
            ):
                return

            if self._is_interrupted_openai_finish_reason(finish_reason):
                interrupted_round_count += 1

            round_index += 1
            current_prompt = self._build_openai_continuation_prompt(normalized_prompt)
            current_metadata = self._build_openai_continuation_metadata(
                normalized_prompt,
                metadata,
                accumulated,
                long_form_request=long_form_request,
                interruption_reason=finish_reason,
                interrupted_round_count=interrupted_round_count,
            )

    async def _run_prompt_with_timeout(
        self,
        prompt: str,
        metadata: dict[str, str] | None,
        timeout_seconds: float,
    ) -> AsyncIterator[str]:
        if self.model_provider == "mock":
            async for token in self.run_prompt(prompt, metadata):
                yield token
            return

        first_token_timeout = max(6.0, timeout_seconds)
        idle_timeout = max(2.0, self._agent_stream_idle_timeout_seconds)
        iterator = self.run_prompt(prompt, metadata).__aiter__()
        got_any_chunk = False
        current_timeout = first_token_timeout

        while True:
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout=current_timeout)
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                if got_any_chunk:
                    raise TimeoutError(
                        f"model stream stalled for {idle_timeout:.1f}s"
                    ) from exc
                raise TimeoutError(
                    f"model first token timeout after {first_token_timeout:.1f}s"
                ) from exc

            got_any_chunk = True
            current_timeout = idle_timeout
            yield chunk

    async def _request_openai_stream_async(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> AsyncIterator[OpenAIStreamItem]:
        queue: asyncio.Queue[object] = asyncio.Queue()
        sentinel = object()
        loop = asyncio.get_running_loop()

        def push(item: object) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        def worker() -> None:
            try:
                for item in self._request_openai_stream_with_retry(prompt, metadata):
                    if item.content or item.finish_reason:
                        push(item)
            except Exception as exc:  # pragma: no cover - runtime 兜底分支
                push(exc)
            finally:
                push(sentinel)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = await queue.get()
            if item is sentinel:
                return
            if isinstance(item, Exception):
                raise item
            if isinstance(item, OpenAIStreamItem):
                yield item

    def _request_openai_stream_with_retry(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> Iterator[OpenAIStreamItem]:
        endpoint = self._openai_base_url.strip() or "https://api.openai.com/v1"
        endpoint = endpoint.rstrip("/") + "/chat/completions"
        payload = self._build_openai_payload(prompt, stream=True, metadata=metadata)
        data = json.dumps(payload).encode("utf-8")

        retryable_http_status = {429, 500, 502, 503, 504}
        last_error: Exception | None = None

        for attempt in range(1, self._openai_max_retries + 1):
            request = urllib_request.Request(
                endpoint,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._openai_api_key}",
                    "Accept": "text/event-stream",
                },
                method="POST",
            )

            emitted_any = False
            try:
                with urllib_request.urlopen(request, timeout=self._openai_http_timeout_seconds) as response:
                    for item in self._iter_openai_sse_items(response):
                        emitted_any = True
                        yield item
                    return
            except urllib_error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="ignore")
                if emitted_any or exc.code not in retryable_http_status or attempt >= self._openai_max_retries:
                    raise RuntimeError(f"openai stream request failed: HTTP {exc.code} {body}") from exc

                retry_after_header = exc.headers.get("Retry-After") if exc.headers else None
                time.sleep(self._compute_retry_delay(attempt, retry_after_header))
                last_error = exc
            except urllib_error.URLError as exc:
                if emitted_any or attempt >= self._openai_max_retries:
                    raise RuntimeError(f"openai stream request failed: {exc.reason}") from exc

                time.sleep(self._compute_retry_delay(attempt, None))
                last_error = exc

        if last_error is not None:
            raise RuntimeError(f"openai stream request failed: {last_error}")
        raise RuntimeError("openai stream request failed: unknown error")

    def _iter_openai_sse_items(self, response: Any) -> Iterator[OpenAIStreamItem]:
        while True:
            raw_line = response.readline()
            if not raw_line:
                return

            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue

            payload_raw = line[5:].strip()
            if payload_raw == "[DONE]":
                return

            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                continue

            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(f"openai stream request failed: {payload['error']}")

            item = self._extract_stream_item(payload)
            if item.content or item.finish_reason:
                yield item

    def _extract_stream_item(self, payload: Any) -> OpenAIStreamItem:
        if not isinstance(payload, dict):
            return OpenAIStreamItem()

        choices = payload.get("choices")
        if not isinstance(choices, list):
            return OpenAIStreamItem()

        fragments: list[str] = []
        finish_reason = ""
        for choice in choices:
            if not isinstance(choice, dict):
                continue

            raw_finish_reason = choice.get("finish_reason")
            if isinstance(raw_finish_reason, str) and raw_finish_reason.strip():
                finish_reason = raw_finish_reason.strip()

            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue

            chunk = self._normalize_content(delta.get("content"))
            if chunk:
                fragments.append(chunk)

        return OpenAIStreamItem(
            content="".join(fragments),
            finish_reason=finish_reason,
        )

    def _normalize_content(self, value: Any) -> str:
        if isinstance(value, str):
            return value

        if isinstance(value, list):
            fragments: list[str] = []
            for item in value:
                if isinstance(item, str):
                    fragments.append(item)
                    continue

                if not isinstance(item, dict):
                    continue

                text = item.get("text")
                if isinstance(text, str):
                    fragments.append(text)
                    continue

                nested_content = item.get("content")
                if isinstance(nested_content, str):
                    fragments.append(nested_content)

            return "".join(fragments)

        return ""

    def _chunk_text(self, text: str, chunk_size: int = 12) -> Iterator[str]:
        if not text.strip():
            return

        for start in range(0, len(text), chunk_size):
            yield text[start : start + chunk_size]

    def _should_continue_openai_response(
        self,
        response_text: str,
        finish_reason: str,
        completed_rounds: int,
        long_form_request: bool,
        done_marker_seen: bool,
    ) -> bool:
        _ = response_text
        if done_marker_seen:
            return False
        if completed_rounds >= self._openai_continuation_max_rounds:
            return False

        normalized_reason = finish_reason.strip().lower()
        if normalized_reason in {"length", "max_tokens", "stream_error"}:
            return True
        if self._is_interrupted_openai_finish_reason(normalized_reason):
            return long_form_request and bool(response_text.strip())
        if normalized_reason in {"tool_calls", "function_call"}:
            return False

        return long_form_request

    def _is_interrupted_openai_finish_reason(self, finish_reason: str) -> bool:
        return finish_reason.strip().lower() in OPENAI_INTERRUPTED_FINISH_REASONS

    def _is_long_form_budget_satisfied(self, response_text: str) -> bool:
        return len(response_text.strip()) >= self._openai_long_form_min_chars

    def _is_long_form_completion_acceptable(self, response_text: str, prompt: str) -> bool:
        text = response_text.strip()
        if not self._is_long_form_budget_satisfied(text):
            return False
        if text.count("```") % 2 == 1:
            return False

        if not text.endswith(OPENAI_TERMINAL_RESPONSE_CHARS):
            return False

        last_words = re.findall(r"[A-Za-z]+", text[-80:].lower())
        if last_words and last_words[-1] in {
            "a",
            "an",
            "and",
            "as",
            "at",
            "by",
            "for",
            "from",
            "in",
            "into",
            "of",
            "on",
            "or",
            "the",
            "to",
            "under",
            "with",
        }:
            return False

        prompt_lower = prompt.lower()
        wants_conclusion = any(
            marker in prompt_lower
            for marker in ("conclusion", "summary", "summarize", "总结", "结论")
        )
        if wants_conclusion:
            conclusion_tail = text[-1600:].lower()
            if not any(
                marker in conclusion_tail
                for marker in ("conclusion", "summary", "in conclusion", "to conclude", "总结", "结论")
            ):
                return False

        return True

    def _build_openai_continuation_prompt(self, original_prompt: str) -> str:
        _ = original_prompt
        return "Continue the previous answer without repeating earlier content."

    def _filter_openai_visible_chunks(
        self,
        marker_buffer: OpenAIDoneMarkerBuffer | None,
        text: str,
    ) -> list[str]:
        if marker_buffer is None:
            return [text] if text else []
        return marker_buffer.feed(text)

    def _flush_openai_visible_chunks(
        self,
        marker_buffer: OpenAIDoneMarkerBuffer | None,
    ) -> list[str]:
        if marker_buffer is None:
            return []
        return marker_buffer.flush()

    def _is_long_form_request(
        self,
        prompt: str,
        metadata: dict[str, str] | None = None,
    ) -> bool:
        candidates = [prompt]
        if metadata:
            user_message = metadata.get(METADATA_CLIENT_USER_MESSAGE_KEY, "")
            if user_message:
                candidates.append(user_message)

        text = " ".join(candidates).strip().lower()
        if len(text) >= 800:
            return True

        long_form_markers = (
            "detailed",
            "detail",
            "in depth",
            "in-depth",
            "comprehensive",
            "thorough",
            "long-form",
            "long form",
            "long answer",
            "essay",
            "full explanation",
            "complete explanation",
            "deep dive",
            "elaborate",
            "explain fully",
            "walk through",
            "write an article",
            "report",
            "详细",
            "深入",
            "全面",
            "完整",
            "长文",
            "展开",
            "详解",
            "讲讲",
            "系统地",
            "文章",
            "报告",
        )
        return any(marker in text for marker in long_form_markers)

    def _build_openai_generation_metadata(
        self,
        prompt: str,
        metadata: dict[str, str] | None,
        long_form_request: bool,
    ) -> dict[str, str] | None:
        if not long_form_request:
            return metadata

        messages = self._build_openai_messages(prompt, metadata)
        messages = self._prepend_openai_system_addendum(
            messages,
            (
                "Honor the user's requested depth and length. This is a long-form "
                "generation request: provide a complete, structured answer with enough "
                "detail to satisfy the request. Use clear sections and cover every "
                "aspect the user asks for. Do not shorten the answer for brevity. "
                f"Unless the user asks for a shorter response, aim for at least "
                f"{self._openai_long_form_min_chars} characters before concluding. "
                "If you cannot finish in one model response, stop without a conclusion "
                "and wait for the runtime to ask you to continue. "
                f"When the answer is fully complete, append {OPENAI_DONE_MARKER} exactly "
                "once at the very end. The marker is for the runtime and must not be "
                "explained."
            ),
        )
        generation_metadata = dict(metadata or {})
        generation_metadata[MODEL_MESSAGES_METADATA_KEY] = json.dumps(
            messages,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return generation_metadata

    def _prepend_openai_system_addendum(
        self,
        messages: list[dict[str, str]],
        addendum: str,
    ) -> list[dict[str, str]]:
        if not messages:
            return [{"role": "system", "content": addendum}]

        normalized = [dict(item) for item in messages]
        if normalized[0].get("role") == "system":
            normalized[0]["content"] = f"{normalized[0].get('content', '')}\n\n{addendum}"
            return normalized

        return [{"role": "system", "content": addendum}, *normalized]

    def _build_openai_continuation_metadata(
        self,
        original_prompt: str,
        metadata: dict[str, str] | None,
        accumulated_response: str,
        long_form_request: bool,
        interruption_reason: str = "",
        interrupted_round_count: int = 0,
    ) -> dict[str, str]:
        generation_metadata = self._build_openai_generation_metadata(
            original_prompt,
            metadata,
            long_form_request=long_form_request,
        )
        messages = self._build_openai_messages(original_prompt, generation_metadata)
        partial_response = accumulated_response.strip()
        if len(partial_response) > 12000:
            partial_response = partial_response[-12000:]

        interruption_instruction = ""
        if self._is_interrupted_openai_finish_reason(interruption_reason):
            interruption_instruction = (
                "The provider ended the previous stream before a natural stopping point. "
                "Do not repeat the interrupted fragment. Continue with the next safe, "
                "high-level part of the answer, then close the remaining requested "
                "sections cleanly. "
            )
            if interrupted_round_count >= 2:
                interruption_instruction += (
                    "Avoid drilling into the repeatedly interrupted example; summarize "
                    "that area at a neutral, high level and move to impacts and conclusion. "
                )

        continuation_messages = [
            *messages,
            {"role": "assistant", "content": partial_response},
            {
                "role": "user",
                "content": (
                    f"{interruption_instruction}"
                    "Continue directly from the previous answer. Do not repeat earlier "
                    "content and do not restart. Write substantial new content, continue "
                    "the next incomplete section, and include later requested sections "
                    "and the conclusion if they have not been covered yet. "
                    f"The current answer has about {len(accumulated_response.strip())} "
                    f"characters; the target minimum is {self._openai_long_form_min_chars}. "
                    f"Do not append {OPENAI_DONE_MARKER} until the complete answer reaches "
                    "that target and all requested aspects are covered. If the answer is "
                    f"fully complete after this continuation, append {OPENAI_DONE_MARKER} "
                    "exactly once at the very end."
                ),
            },
        ]
        continuation_metadata = dict(metadata or {})
        continuation_metadata[MODEL_MESSAGES_METADATA_KEY] = json.dumps(
            continuation_messages,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return continuation_metadata

    def _trim_incomplete_openai_fragment(self, text: str) -> str:
        stripped = text.rstrip()
        if not stripped:
            return ""
        if stripped.endswith(OPENAI_TERMINAL_RESPONSE_CHARS):
            return text

        last_terminal_index = max(
            stripped.rfind(mark) for mark in OPENAI_STRONG_TERMINAL_RESPONSE_CHARS
        )
        if last_terminal_index < 0:
            return ""

        return stripped[: last_terminal_index + 1]

    def _trim_continuation_overlap(self, existing_text: str, continuation_text: str) -> str:
        if not existing_text or not continuation_text:
            return continuation_text

        existing_tail = existing_text[-1000:]
        max_overlap = min(len(existing_tail), len(continuation_text), 400)
        for size in range(max_overlap, 19, -1):
            if existing_tail.endswith(continuation_text[:size]):
                return continuation_text[size:]
        return continuation_text

    def _build_openai_payload(
        self,
        prompt: str,
        stream: bool,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "model": self._openai_model,
            "messages": self._build_openai_messages(prompt, metadata),
            "temperature": self._openai_temperature,
            "max_tokens": self._openai_max_tokens,
            "stream": stream,
        }

    def _build_openai_messages(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> list[dict[str, str]]:
        if metadata:
            raw_messages = metadata.get(MODEL_MESSAGES_METADATA_KEY, "").strip()
            if raw_messages:
                try:
                    parsed = json.loads(raw_messages)
                    if isinstance(parsed, list):
                        normalized: list[dict[str, str]] = []
                        for item in parsed:
                            if not isinstance(item, dict):
                                continue

                            role = item.get("role")
                            content = item.get("content")
                            if (
                                isinstance(role, str)
                                and role in {"system", "user", "assistant"}
                                and isinstance(content, str)
                                and content.strip()
                            ):
                                normalized.append(
                                    {
                                        "role": role,
                                        "content": content,
                                    }
                                )

                        if normalized:
                            return normalized
                except json.JSONDecodeError:
                    pass

        return [
            {
                "role": "system",
                "content": "You are Synapse runtime. Keep responses concise and practical.",
            },
            {"role": "user", "content": prompt},
        ]

    def _request_openai_completion(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> str:
        return self._request_openai_completion_result(prompt, metadata).content

    def _request_openai_completion_result(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> OpenAICompletionResult:
        endpoint = self._openai_base_url.strip() or "https://api.openai.com/v1"
        endpoint = endpoint.rstrip("/") + "/chat/completions"

        payload = self._build_openai_payload(prompt, stream=False, metadata=metadata)

        data = json.dumps(payload).encode("utf-8")
        response_payload = self._perform_request_with_retry(endpoint, data)

        choices = response_payload.get("choices") or []
        if not choices:
            return OpenAICompletionResult()

        choice = choices[0]
        finish_reason = ""
        raw_finish_reason = choice.get("finish_reason")
        if isinstance(raw_finish_reason, str):
            finish_reason = raw_finish_reason.strip()

        message = choice.get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        texts.append(text)
            content = "\n".join(texts)

        if isinstance(content, str):
            return OpenAICompletionResult(
                content=content.strip(),
                finish_reason=finish_reason,
            )
        return OpenAICompletionResult(finish_reason=finish_reason)

    def _perform_request_with_retry(self, endpoint: str, data: bytes) -> dict:
        retryable_http_status = {429, 500, 502, 503, 504}
        last_error: Exception | None = None

        for attempt in range(1, self._openai_max_retries + 1):
            request = urllib_request.Request(
                endpoint,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._openai_api_key}",
                },
                method="POST",
            )

            try:
                with urllib_request.urlopen(request, timeout=self._openai_http_timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib_error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="ignore")
                if exc.code not in retryable_http_status or attempt >= self._openai_max_retries:
                    raise RuntimeError(f"openai request failed: HTTP {exc.code} {body}") from exc

                retry_after_header = exc.headers.get("Retry-After") if exc.headers else None
                delay = self._compute_retry_delay(attempt, retry_after_header)
                time.sleep(delay)
                last_error = exc
            except urllib_error.URLError as exc:
                if attempt >= self._openai_max_retries:
                    raise RuntimeError(f"openai request failed: {exc.reason}") from exc

                delay = self._compute_retry_delay(attempt, None)
                time.sleep(delay)
                last_error = exc

        if last_error is not None:
            raise RuntimeError(f"openai request failed: {last_error}")
        raise RuntimeError("openai request failed: unknown error")

    def _compute_retry_delay(self, attempt: int, retry_after_header: str | None) -> float:
        if retry_after_header:
            try:
                parsed = float(retry_after_header)
                if parsed > 0:
                    return min(parsed, 20.0)
            except ValueError:
                pass

        # 这里线性退避已经足够，并且可以限制总等待时间。
        return min(self._openai_retry_backoff_seconds * attempt, 10.0)

    def _build_response(self, prompt: str) -> str:
        # 统一 mock 响应格式，保证本地测试输出稳定。
        normalized_prompt = " ".join(prompt.strip().split())
        if not normalized_prompt:
            normalized_prompt = "empty request"

        return (
            "Synapse acknowledged your request: "
            f"{normalized_prompt}. "
            "Next milestone is replacing this mock runtime with real model routing."
        )
