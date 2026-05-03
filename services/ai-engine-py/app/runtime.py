import asyncio
import ast
import json
import pathlib
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterator
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from app.tools import (
    ToolAuditLogger,
    ToolCall,
    ToolContext,
    ToolPolicy,
    ToolRegistry,
    ToolResult,
    register_builtin_tools,
)


MODEL_MESSAGES_METADATA_KEY = "model_messages_json"
METADATA_AGENT_ENABLED_KEY = "agent_enabled"
METADATA_APPROVAL_GRANTED_KEY = "approval_granted"
METADATA_APPROVED_TOOLS_KEY = "approved_tools"
METADATA_AUTH_USER_ROLE_KEY = "auth_user_role"
METADATA_AUTH_USERNAME_KEY = "auth_username"
METADATA_MEMORY_WRITE_ENABLED_KEY = "memory_write_enabled"
METADATA_AGENT_RESUME_STEP_KEY = "agent_resume_step_index"
METADATA_AGENT_REQUIRED_TOOL_KEY = "agent_required_tool"
METADATA_AGENT_RESUME_REQUESTED_BY_KEY = "agent_resume_requested_by"
METADATA_CLIENT_USER_MESSAGE_KEY = "user_message"
AGENT_INFO_SCHEMA = "synapse.agent.info.v1"


@dataclass(frozen=True)
class RuntimeStreamItem:
    kind: str
    message: str = ""
    token: str = ""


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


class AgentMemoryStore:
    """按用户 ID 分组的简单文件型长期记忆存储。"""

    def __init__(self, file_path: str, max_entries_per_user: int) -> None:
        self._path = pathlib.Path(file_path).expanduser() if file_path.strip() else None
        self._max_entries_per_user = max(1, max_entries_per_user)
        self._lock = threading.Lock()

    def recall(self, user_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        if self._path is None:
            return []

        normalized_user_id = user_id.strip().lower()
        if not normalized_user_id:
            return []

        normalized_limit = max(0, limit)
        if normalized_limit == 0:
            return []

        with self._lock:
            document = self._load_document()

        records = document.get("users", {}).get(normalized_user_id, [])
        if not isinstance(records, list):
            return []

        query_tokens = self._tokenize(query)
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for item in records:
            if not isinstance(item, dict):
                continue

            content = " ".join(
                [
                    str(item.get("prompt", "")),
                    str(item.get("summary", "")),
                    str(item.get("final_response_preview", "")),
                ]
            )
            overlap = len(self._tokenize(content).intersection(query_tokens))
            created_at = int(item.get("created_at_unix_ms", 0) or 0)
            scored.append((overlap, created_at, item))

        scored.sort(key=lambda value: (value[0], value[1]), reverse=True)

        selected: list[dict[str, Any]] = []
        for overlap, _, record in scored:
            if overlap <= 0 and selected:
                break
            selected.append(record)
            if len(selected) >= normalized_limit:
                break

        if selected:
            return selected

        # 没有词面命中时，返回最近记忆作为兜底上下文。
        latest = sorted(
            (item for item in records if isinstance(item, dict)),
            key=lambda value: int(value.get("created_at_unix_ms", 0) or 0),
            reverse=True,
        )
        return latest[:normalized_limit]

    def append(
        self,
        user_id: str,
        prompt: str,
        summary: str,
        final_response_preview: str,
        estimated_success: float,
        created_at_unix_ms: int,
    ) -> None:
        if self._path is None:
            return

        normalized_user_id = user_id.strip().lower()
        if not normalized_user_id:
            return

        record = {
            "prompt": prompt.strip(),
            "summary": summary.strip(),
            "final_response_preview": final_response_preview.strip(),
            "estimated_success": round(max(0.0, min(1.0, estimated_success)), 3),
            "created_at_unix_ms": int(created_at_unix_ms),
        }

        with self._lock:
            document = self._load_document()
            users = document.setdefault("users", {})
            if not isinstance(users, dict):
                users = {}
                document["users"] = users

            history = users.setdefault(normalized_user_id, [])
            if not isinstance(history, list):
                history = []

            history.append(record)
            users[normalized_user_id] = history[-self._max_entries_per_user :]

            self._save_document(document)

    def _load_document(self) -> dict[str, Any]:
        if self._path is None or not self._path.exists():
            return {"version": 1, "users": {}}

        try:
            raw = self._path.read_text(encoding="utf-8")
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                return decoded
        except Exception:
            pass

        return {"version": 1, "users": {}}

    def _save_document(self, document: dict[str, Any]) -> None:
        if self._path is None:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(document, ensure_ascii=True, separators=(",", ":"))
        self._path.write_text(serialized, encoding="utf-8")

    def _tokenize(self, text: str) -> set[str]:
        normalized = text.strip().lower()
        if not normalized:
            return set()

        return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", normalized))


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
        agent_enabled_default: bool = True,
        agent_max_plan_steps: int = 6,
        agent_require_approval_for_high_risk: bool = True,
        agent_memory_file: str = "",
        agent_memory_max_entries_per_user: int = 80,
        agent_memory_recall_limit: int = 3,
        agent_tool_http_allowlist: tuple[str, ...] | list[str] | None = None,
        agent_tool_http_timeout_seconds: float = 12.0,
        agent_enable_code_execution: bool = False,
        agent_tool_policy_json: str = "",
        agent_tool_audit_log_file: str = "",
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
        self._agent_generation_timeout_seconds = min(
            30.0, max(15.0, self._openai_http_timeout_seconds * 0.55)
        )
        self._agent_rescue_timeout_seconds = min(
            14.0, max(8.0, self._openai_http_timeout_seconds * 0.3)
        )
        self._agent_enabled_default = agent_enabled_default
        self._agent_max_plan_steps = max(1, agent_max_plan_steps)
        self._agent_require_approval_for_high_risk = agent_require_approval_for_high_risk
        self._agent_memory_recall_limit = max(1, agent_memory_recall_limit)
        self._agent_memory_store = AgentMemoryStore(
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
        )

        default_approval_required: set[str] = set()
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

        self._tool_policy = ToolPolicy.from_json(
            raw_json=agent_tool_policy_json,
            default_role_allow={
                "admin": {"*"},
                "user": {
                    "retrieval",
                    "calculator",
                    "browser_fetch",
                    "http_api",
                    "json_echo",
                },
            },
            default_approval_required=default_approval_required,
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
        resume_step_index = self._read_int(
            metadata_map.get(METADATA_AGENT_RESUME_STEP_KEY), default_value=1
        )
        if resume_step_index < 1:
            resume_step_index = 1

        short_context = self._extract_short_context(metadata_map)
        context_url = self._extract_latest_context_url(normalized_prompt, metadata_map)
        recalled_memories = self._agent_memory_store.recall(
            normalized_user_id, normalized_prompt, self._agent_memory_recall_limit
        )

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
            self._agent_memory_store.append(
                user_id=normalized_user_id,
                prompt=normalized_prompt,
                summary=" | ".join(step_summaries[-3:]),
                final_response_preview=final_response[:400],
                estimated_success=evaluation.estimated_success,
                created_at_unix_ms=int(time.time() * 1000),
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
            payload["tool_description"] = tool.description
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

        if self._tool_requires_approval(decision.tool_name) and not (
            approval_granted or decision.tool_name in approved_tools
        ):
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
                "tool_input": decision.tool_input,
                "resume_step_index": decision.step_index,
                "reason": outcome.reason,
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

        if failed_decision.tool_name in {"browser_fetch", "http_api"}:
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
            if "api" in lowered or "/api/" in normalized_url or normalized_url.endswith(".json"):
                return ("http_api", url)
            return ("browser_fetch", url)

        if any(token in lowered for token in ("python", "script", "code", "代码", "脚本", "执行")):
            candidate_expression = self._extract_math_expression(objective) or objective
            return ("code_exec", candidate_expression)

        math_expression = self._extract_math_expression(objective)
        if math_expression:
            return ("calculator", math_expression)

        if any(token in lowered for token in ("search", "lookup", "retrieve", "查询", "搜", "检索")):
            return ("retrieval", objective)

        if context_url and self._is_web_followup_intent(objective):
            normalized_url = context_url.lower()
            if "api" in lowered or "/api/" in normalized_url or normalized_url.endswith(".json"):
                return ("http_api", context_url)
            return ("browser_fetch", context_url)

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
        )

        return result

    def _build_tool_call_arguments(self, tool_name: str, tool_input: str) -> dict[str, Any]:
        # 该适配器保留旧 selector 契约，同时为每个内置工具提供其
        # input_schema 声明的结构化参数键。
        normalized_name = tool_name.strip().lower()
        if normalized_name == "calculator":
            return {"expression": tool_input}
        if normalized_name in {"browser_fetch", "http_api"}:
            return {"url": tool_input}
        if normalized_name == "code_exec":
            return {"code": tool_input}
        if normalized_name == "json_echo":
            return {"payload": tool_input}
        if normalized_name == "retrieval":
            return {"query": tool_input}
        return {"input": tool_input}

    def _execute_http_tool(
        self,
        url: str,
        parse_json: bool,
    ) -> ToolResult:
        tool_name = "http_api" if parse_json else "browser_fetch"
        normalized_url = self._normalize_tool_url(url)
        if not normalized_url:
            return ToolResult(ok=False, output=f"{tool_name} failed: URL is required")

        parsed = urllib_parse.urlparse(normalized_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ToolResult(ok=False, output=f"{tool_name} failed: unsupported URL")

        host = (parsed.hostname or "").strip().lower()
        if not self._is_host_allowed(host):
            return ToolResult(
                ok=False,
                output=f"{tool_name} blocked: host {host} is not in allowlist",
            )

        request = urllib_request.Request(
            normalized_url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "synapse-agent-runtime/1.0",
            },
            method="GET",
        )

        raw = ""
        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                with urllib_request.urlopen(request, timeout=self._agent_tool_http_timeout_seconds) as response:
                    raw = response.read(65536).decode("utf-8", errors="ignore")
                break
            except urllib_error.URLError as exc:
                if attempt >= attempts:
                    return ToolResult(ok=False, output=f"{tool_name} failed: {exc}")
                time.sleep(0.35 * attempt)
            except Exception as exc:
                if attempt >= attempts:
                    return ToolResult(ok=False, output=f"{tool_name} failed: {exc}")
                time.sleep(0.35 * attempt)

        if parse_json:
            try:
                parsed_json = json.loads(raw)
                compact = json.dumps(parsed_json, ensure_ascii=True)
                return ToolResult(ok=True, output=f"{tool_name} response: {compact[:2400]}")
            except json.JSONDecodeError:
                return ToolResult(ok=True, output=f"{tool_name} response: {raw[:2400]}")

        stripped = re.sub(r"<[^>]+>", " ", raw)
        compact = " ".join(stripped.split())
        return ToolResult(ok=True, output=f"{tool_name} response: {compact[:2400]}")

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
            if tool_name not in {"browser_fetch", "http_api"}:
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

        emitted_any = False
        try:
            async for chunk in self._request_openai_stream_async(
                normalized_prompt, metadata
            ):
                emitted_any = True
                yield chunk
        except Exception:
            if emitted_any:
                raise

            # 兼容不支持 stream=true 的 OpenAI 兼容网关，降级到普通 completions。
            response_text = await asyncio.to_thread(
                self._request_openai_completion, normalized_prompt, metadata
            )
            if not response_text:
                yield "(empty response)"
                return

            for chunk in self._chunk_text(response_text):
                yield chunk

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
        idle_timeout = min(15.0, max(6.0, timeout_seconds * 0.5))
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
    ) -> AsyncIterator[str]:
        queue: asyncio.Queue[object] = asyncio.Queue()
        sentinel = object()
        loop = asyncio.get_running_loop()

        def push(item: object) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        def worker() -> None:
            try:
                for chunk in self._request_openai_stream_with_retry(prompt, metadata):
                    if chunk:
                        push(chunk)
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
            yield str(item)

    def _request_openai_stream_with_retry(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> Iterator[str]:
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
                    for chunk in self._iter_openai_sse_chunks(response):
                        emitted_any = True
                        yield chunk
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

    def _iter_openai_sse_chunks(self, response: Any) -> Iterator[str]:
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

            chunk = self._extract_stream_chunk(payload)
            if chunk:
                yield chunk

    def _extract_stream_chunk(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""

        choices = payload.get("choices")
        if not isinstance(choices, list):
            return ""

        fragments: list[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue

            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue

            chunk = self._normalize_content(delta.get("content"))
            if chunk:
                fragments.append(chunk)

        return "".join(fragments)

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
        normalized = text.strip()
        if not normalized:
            return

        for start in range(0, len(normalized), chunk_size):
            yield normalized[start : start + chunk_size]

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
        endpoint = self._openai_base_url.strip() or "https://api.openai.com/v1"
        endpoint = endpoint.rstrip("/") + "/chat/completions"

        payload = self._build_openai_payload(prompt, stream=False, metadata=metadata)

        data = json.dumps(payload).encode("utf-8")
        response_payload = self._perform_request_with_retry(endpoint, data)

        choices = response_payload.get("choices") or []
        if not choices:
            return ""

        message = choices[0].get("message") or {}
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
            return content.strip()
        return ""

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
