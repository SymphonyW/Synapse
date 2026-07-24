import asyncio
import json
import re
from typing import Any, AsyncIterator, Iterable, Iterator

from app.memory import MemoryRecallHit, MemoryRecord
from app.runtime import (
    MODEL_MESSAGES_METADATA_KEY,
    AgentRuntime,
    OpenAICompletionResult,
    OpenAIStreamItem,
    RuntimeStreamItem,
)
from app.tools import BaseAgentTool, ToolCall, ToolContext, ToolProviderPolicy, ToolResult


def collect_runtime_events(
    runtime: AgentRuntime,
    prompt: str,
    metadata: dict[str, str] | None = None,
    task_id: str = "runtime-characterization-task",
    user_id: str = "runtime-characterization-user",
) -> list[RuntimeStreamItem]:
    return asyncio.run(
        collect_runtime_events_async(
            runtime=runtime,
            prompt=prompt,
            metadata=metadata,
            task_id=task_id,
            user_id=user_id,
        )
    )


async def collect_runtime_events_async(
    runtime: AgentRuntime,
    prompt: str,
    metadata: dict[str, str] | None = None,
    task_id: str = "runtime-characterization-task",
    user_id: str = "runtime-characterization-user",
) -> list[RuntimeStreamItem]:
    events: list[RuntimeStreamItem] = []
    async for event in runtime.run_task(
        task_id=task_id,
        user_id=user_id,
        prompt=prompt,
        metadata=metadata or {},
    ):
        events.append(event)
    return events


def decoded_infos(events: Iterable[RuntimeStreamItem]) -> list[dict[str, Any]]:
    return [
        json.loads(event.message)
        for event in events
        if event.kind == "info" and event.message
    ]


def event_phases(events: Iterable[RuntimeStreamItem]) -> list[str]:
    return [str(item.get("agent_event", "")) for item in decoded_infos(events)]


def phase_payloads(
    events: Iterable[RuntimeStreamItem],
    phase: str,
) -> list[dict[str, Any]]:
    return [
        dict(item.get("payload", {}))
        for item in decoded_infos(events)
        if item.get("agent_event") == phase
    ]


def first_phase_payload(events: Iterable[RuntimeStreamItem], phase: str) -> dict[str, Any]:
    payloads = phase_payloads(events, phase)
    if not payloads:
        raise AssertionError(f"missing runtime info phase: {phase}")
    return payloads[0]


def token_text(events: Iterable[RuntimeStreamItem]) -> str:
    return "".join(event.token for event in events if event.kind == "token")


def kind_counts(events: Iterable[RuntimeStreamItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.kind] = counts.get(event.kind, 0) + 1
    return counts


def normalize_events(events: Iterable[RuntimeStreamItem]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for event in events:
        if event.kind == "token":
            normalized.append({"kind": "token", "token": event.token})
            continue
        if event.kind in {"info", "pause"} and event.message:
            normalized.append(
                {
                    "kind": event.kind,
                    "message": _strip_volatile(json.loads(event.message)),
                }
            )
            continue
        normalized.append({"kind": event.kind})
    return normalized


def approved_tool_call(
    tool_name: str,
    tool_input: str,
    risk_level: str = "high",
    resume_step_index: int = 1,
) -> str:
    return json.dumps(
        {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "risk_level": risk_level,
            "reason": "characterization approval",
            "resume_step_index": resume_step_index,
        },
        separators=(",", ":"),
    )


class RecordingTool(BaseAgentTool):
    def __init__(
        self,
        name: str,
        output: str,
        ok: bool = True,
        error_code: str = "test_failure",
        retryable: bool = False,
        risk_level: str = "low",
        requires_approval: bool = False,
        metadata: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = f"Recording tool {name}"
        self.input_schema = {
            "type": "object",
            "properties": {"payload": {"type": "string"}},
            "additionalProperties": False,
        }
        self.risk_level = risk_level
        self.requires_approval = requires_approval
        self.calls: list[ToolCall] = []
        self.contexts: list[ToolContext] = []
        self._output = output
        self._ok = ok
        self._error_code = error_code
        self._retryable = retryable
        self._metadata = dict(metadata or {})
        self._details = dict(details or {})

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        self.calls.append(call)
        self.contexts.append(context)
        if self._ok:
            return ToolResult.success(
                self._output.format(input=call.argument_text("payload", "input")),
                metadata=self._metadata,
            )
        return ToolResult.failure(
            self._output,
            code=self._error_code,
            retryable=self._retryable,
            details=self._details,
        )


class RecordingToolProvider:
    def __init__(
        self,
        tools: Iterable[RecordingTool],
        provider_name: str = "characterization",
        role_allow: dict[str, set[str]] | None = None,
        approval_required: set[str] | None = None,
        disabled_tools: set[str] | None = None,
    ) -> None:
        self.provider_name = provider_name
        self._tools = tuple(tools)
        tool_names = {tool.name for tool in self._tools}
        self._policy = ToolProviderPolicy(
            role_allow=role_allow or {"user": set(tool_names), "admin": {"*"}},
            approval_required=set(approval_required or set()),
            disabled_tools=set(disabled_tools or set()),
        )

    def discover_tools(self) -> tuple[RecordingTool, ...]:
        return self._tools

    def policy_defaults(self) -> ToolProviderPolicy:
        return self._policy


class RecordingMemoryStore:
    def __init__(self) -> None:
        self.records: dict[str, list[MemoryRecord]] = {}
        self.recall_calls: list[tuple[str, str, int]] = []
        self.write_calls: list[MemoryRecord] = []
        self.delete_calls: list[tuple[str, str]] = []

    def seed(
        self,
        user_id: str,
        content: str,
        summary: str,
        source_task_id: str,
        memory_id: str,
        importance: float = 0.9,
        created_at: int = 100,
    ) -> MemoryRecord:
        record = MemoryRecord(
            memory_id=memory_id,
            user_id=_normalize_user_id(user_id),
            content=content,
            summary=summary,
            source_task_id=source_task_id,
            importance=importance,
            created_at=created_at,
        )
        self.records.setdefault(record.user_id, []).append(record)
        return record

    def memory_write(self, record: MemoryRecord) -> MemoryRecord | None:
        normalized_user_id = _normalize_user_id(record.user_id)
        if not normalized_user_id:
            return None
        written = MemoryRecord(
            memory_id=record.memory_id or f"memory-{len(self.write_calls) + 1}",
            user_id=normalized_user_id,
            content=record.content,
            summary=record.summary,
            source_task_id=record.source_task_id,
            importance=record.importance,
            created_at=record.created_at or 1,
        )
        self.write_calls.append(written)
        self.records.setdefault(written.user_id, []).append(written)
        return written

    def memory_recall(self, user_id: str, query: str, limit: int) -> list[MemoryRecallHit]:
        normalized_user_id = _normalize_user_id(user_id)
        self.recall_calls.append((normalized_user_id, query, limit))
        if limit <= 0:
            return []

        query_tokens = _tokens(query)
        hits: list[MemoryRecallHit] = []
        for record in self.records.get(normalized_user_id, []):
            matched_terms = tuple(
                sorted(_tokens(f"{record.summary} {record.content}").intersection(query_tokens))
            )
            if not matched_terms:
                continue
            hits.append(
                MemoryRecallHit(
                    record=record,
                    score=float(len(matched_terms)),
                    matched_terms=matched_terms,
                )
            )
        return hits[:limit]

    def memory_delete(self, user_id: str, memory_id: str) -> bool:
        normalized_user_id = _normalize_user_id(user_id)
        self.delete_calls.append((normalized_user_id, memory_id))
        records = self.records.get(normalized_user_id, [])
        kept = [record for record in records if record.memory_id != memory_id]
        self.records[normalized_user_id] = kept
        return len(kept) != len(records)

    def memory_list(self, user_id: str, limit: int) -> list[MemoryRecord]:
        return self.records.get(_normalize_user_id(user_id), [])[: max(0, limit)]


class ScriptedOpenAIRuntime(AgentRuntime):
    def __init__(
        self,
        rounds: list[list[OpenAIStreamItem | BaseException]],
        continuation_max_rounds: int = 2,
        long_form_min_chars: int = 0,
        completion_results: list[OpenAICompletionResult] | None = None,
    ) -> None:
        super().__init__(
            model_provider="openai",
            openai_api_key="test-key",
            openai_continuation_max_rounds=continuation_max_rounds,
            openai_long_form_min_chars=long_form_min_chars,
            agent_tool_audit_log_file="",
        )
        self.rounds = list(rounds)
        self.completion_results = list(completion_results or [])
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.completion_calls: list[tuple[str, dict[str, str]]] = []

    def _request_openai_stream_with_retry(
        self,
        prompt: str,
        metadata: dict[str, str] | None = None,
    ) -> Iterator[OpenAIStreamItem]:
        self.calls.append((prompt, dict(metadata or {})))
        if not self.rounds:
            return
        round_items = self.rounds.pop(0)
        for item in round_items:
            if isinstance(item, BaseException):
                raise item
            yield item

    def _request_openai_completion_result(
        self,
        prompt: str,
        metadata: dict[str, str] | None = None,
    ) -> OpenAICompletionResult:
        self.completion_calls.append((prompt, dict(metadata or {})))
        if self.completion_results:
            return self.completion_results.pop(0)
        return OpenAICompletionResult()


class FailingPromptRuntime(AgentRuntime):
    def __init__(self, error: Exception) -> None:
        super().__init__(
            model_provider="openai",
            openai_api_key="test-key",
            agent_tool_audit_log_file="",
        )
        self.error = error
        self.prompt_calls: list[tuple[str, dict[str, str], float]] = []

    async def _run_prompt_with_timeout(
        self,
        prompt: str,
        metadata: dict[str, str] | None,
        timeout_seconds: float,
    ) -> AsyncIterator[str]:
        self.prompt_calls.append((prompt, dict(metadata or {}), timeout_seconds))
        raise self.error
        if False:
            yield ""


async def collect_openai_text(runtime: AgentRuntime, prompt: str, metadata: dict[str, str] | None = None) -> str:
    chunks: list[str] = []
    async for chunk in runtime._run_openai(prompt, metadata):
        chunks.append(chunk)
    return "".join(chunks)


def _strip_volatile(value: Any) -> Any:
    volatile_keys = {
        "created_at",
        "duration_ms",
        "memory_id",
        "tool_call_id",
        "call_id",
    }
    if isinstance(value, dict):
        return {
            key: _strip_volatile(item)
            for key, item in value.items()
            if key not in volatile_keys
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _normalize_user_id(user_id: str) -> str:
    return " ".join(user_id.strip().lower().split())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_]+", text.lower()))
