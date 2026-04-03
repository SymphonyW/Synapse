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


class AgentMemoryStore:
    """Simple file-backed long-term memory store keyed by user id."""

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

        # No lexical hit: return the latest memories as fallback context.
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

        # Support semantic aliases while using the same OpenAI-compatible transport path.
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
            for tool_name in self._tool_registry.names():
                tool = self._tool_registry.get(tool_name)
                if tool is not None and getattr(tool, "high_risk", False):
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
            async for token in self.run_prompt(prompt, metadata_map):
                yield RuntimeStreamItem(kind="token", token=token)
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

            tool_name, tool_input = self._select_tool(step.objective, normalized_prompt)
            if tool_name != "none":
                yield RuntimeStreamItem(
                    kind="info",
                    message=self._encode_agent_info(
                        phase="decide",
                        payload={
                            "step_index": step.index,
                            "tool": tool_name,
                            "tool_input": tool_input,
                        },
                    ),
                )

            if tool_name == "none":
                observation = "no external tool required"
                completed_steps += 1
            elif not self._is_tool_allowed_for_role(tool_name, actor_role):
                blocked_actions += 1
                observation = f"tool {tool_name} is blocked for role {actor_role}"
                self._tool_audit.log(
                    task_id=task_id,
                    user_id=normalized_user_id,
                    user_role=actor_role,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    outcome=observation,
                    ok=False,
                    duration_ms=0,
                    reason="policy_blocked",
                )
                yield RuntimeStreamItem(
                    kind="info",
                    message=self._encode_agent_info(
                        phase="policy_blocked",
                        payload={
                            "step_index": step.index,
                            "tool": tool_name,
                            "role": actor_role,
                        },
                    ),
                )
            elif self._tool_requires_approval(tool_name) and not (
                approval_granted or tool_name in approved_tools
            ):
                blocked_actions += 1
                observation = f"tool {tool_name} requires explicit approval"
                self._tool_audit.log(
                    task_id=task_id,
                    user_id=normalized_user_id,
                    user_role=actor_role,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    outcome=observation,
                    ok=False,
                    duration_ms=0,
                    reason="approval_required",
                )
                pause_payload = {
                    "step_index": step.index,
                    "tool": tool_name,
                    "resume_step_index": step.index,
                    "hint": "call task approve endpoint to resume execution",
                }
                yield RuntimeStreamItem(
                    kind="info",
                    message=self._encode_agent_info(
                        phase="approval_required",
                        payload=pause_payload,
                    ),
                )
                yield RuntimeStreamItem(
                    kind="pause",
                    message=self._encode_agent_info(
                        phase="paused",
                        payload={
                            "reason": observation,
                            "tool": tool_name,
                            "resume_step_index": step.index,
                        },
                    ),
                )
                return
            else:
                tool_call_count += 1
                result = await asyncio.to_thread(
                    self._execute_tool,
                    task_id,
                    normalized_user_id,
                    actor_role,
                    tool_name,
                    tool_input,
                    normalized_prompt,
                    metadata_map,
                    recalled_memories,
                )
                if result.ok:
                    completed_steps += 1
                    tool_success_count += 1
                observation = result.output

            yield RuntimeStreamItem(
                kind="info",
                message=self._encode_agent_info(
                    phase="observe",
                    payload={
                        "step_index": step.index,
                        "observation": observation,
                    },
                ),
            )

            reflection = self._reflect_step(step.objective, observation)
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

        evaluation = self._evaluate_task(
            total_steps=len(plan_steps),
            completed_steps=completed_steps,
            tool_call_count=tool_call_count,
            tool_success_count=tool_success_count,
            blocked_actions=blocked_actions,
        )

        final_response_chunks: list[str] = []
        synthesis_failed = False
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
            synthesis_prompt = self._build_user_facing_prompt(
                prompt=normalized_prompt,
                short_context=short_context,
                recalled_memories=recalled_memories,
                step_summaries=step_summaries,
                evaluation=evaluation,
            )
            try:
                async for chunk in self.run_prompt(synthesis_prompt, metadata=None):
                    if not chunk:
                        continue
                    final_response_chunks.append(chunk)
                    yield RuntimeStreamItem(kind="token", token=chunk)
            except Exception:
                synthesis_failed = True

        final_response = "".join(final_response_chunks).strip()
        if not final_response:
            synthesis_failed = True

        if synthesis_failed:
            fallback_response = self._build_diagnostic_fallback(
                normalized_prompt,
                step_summaries,
                evaluation,
            )
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

    def _encode_agent_info(self, phase: str, payload: dict[str, Any]) -> str:
        message = {
            "agent_event": phase,
            "payload": payload,
        }
        return json.dumps(message, ensure_ascii=True, separators=(",", ":"))

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

    def _select_tool(self, objective: str, prompt: str) -> tuple[str, str]:
        lowered = objective.lower()

        if any(
            token in lowered
            for token in ("memory", "history", "context", "previous", "recall", "检索", "历史", "上下文")
        ):
            return ("retrieval", objective)

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

        return ("none", "")

    def _extract_first_url(self, text: str) -> str:
        match = re.search(r"https?://[^\s\]\[\)\(<>]+", text)
        if not match:
            return ""

        return match.group(0)

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
        result = tool.execute(tool_input, context)
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

    def _execute_http_tool(
        self,
        url: str,
        parse_json: bool,
    ) -> ToolResult:
        tool_name = "http_api" if parse_json else "browser_fetch"
        normalized_url = url.strip()
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

        try:
            with urllib_request.urlopen(request, timeout=self._agent_tool_http_timeout_seconds) as response:
                raw = response.read(8192).decode("utf-8", errors="ignore")
        except Exception as exc:
            return ToolResult(ok=False, output=f"{tool_name} failed: {exc}")

        if parse_json:
            try:
                parsed_json = json.loads(raw)
                compact = json.dumps(parsed_json, ensure_ascii=True)
                return ToolResult(ok=True, output=f"{tool_name} response: {compact[:1200]}")
            except json.JSONDecodeError:
                return ToolResult(ok=True, output=f"{tool_name} response: {raw[:1200]}")

        stripped = re.sub(r"<[^>]+>", " ", raw)
        compact = " ".join(stripped.split())
        return ToolResult(ok=True, output=f"{tool_name} response: {compact[:1200]}")

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
            except Exception as exc:  # pragma: no cover - runtime safety branch
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

        # Linear backoff is enough here and keeps total wait bounded.
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
