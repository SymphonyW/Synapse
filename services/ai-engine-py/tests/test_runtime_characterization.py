import asyncio
import io
import json
import pathlib
import sys
import unittest
from unittest.mock import patch
from urllib import error as urllib_error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from app.runtime import (
    AgentRuntime,
    MODEL_MESSAGES_METADATA_KEY,
    OPENAI_DONE_MARKER,
    OpenAICompletionResult,
    OpenAIStreamItem,
    RuntimeStreamItem,
)
from app.service import AgentRuntimeService
from synapse.v1 import agent_pb2

from runtime_fixtures import (
    FailingPromptRuntime,
    RecordingMemoryStore,
    RecordingTool,
    RecordingToolProvider,
    ScriptedOpenAIRuntime,
    approved_tool_call,
    collect_openai_text,
    collect_runtime_events,
    event_phases,
    first_phase_payload,
    token_text,
)


class RuntimeCharacterizationTests(unittest.TestCase):
    def test_mock_task_event_order_and_service_terminal_event(self) -> None:
        runtime = AgentRuntime(model_provider="mock", agent_tool_audit_log_file="")

        service_events = asyncio.run(
            _collect_service_events(
                runtime,
                prompt="write a short runtime status update",
                metadata={
                    "agent_enabled": "true",
                    "memory_write_enabled": "false",
                },
            )
        )

        event_types = [event.type for event in service_events]
        self.assertEqual(event_types[0], agent_pb2.AGENT_EVENT_TYPE_STARTED)
        self.assertEqual(event_types[-1], agent_pb2.AGENT_EVENT_TYPE_COMPLETED)
        self.assertEqual(event_types.count(agent_pb2.AGENT_EVENT_TYPE_COMPLETED), 1)
        self.assertEqual(event_types.count(agent_pb2.AGENT_EVENT_TYPE_FAILED), 0)
        self.assertIn(agent_pb2.AGENT_EVENT_TYPE_TOKEN, event_types)

        phases = _service_info_phases(service_events)
        _assert_ordered_subsequence(
            self,
            phases,
            [
                "perceive",
                "memory_recall",
                "plan",
                "act",
                "tool_skipped",
                "observe",
                "reflect",
                "evaluate",
            ],
        )

    def test_tool_success_emits_trace_and_evaluation_success(self) -> None:
        tool = RecordingTool(
            name="deterministic_tool",
            output='deterministic result: {{"answer":"fixed","input":"{input}"}}',
            metadata={"fixture": "deterministic"},
        )
        runtime = _runtime_with_tools(tool)

        events = collect_runtime_events(
            runtime,
            prompt="use the deterministic fixture",
            metadata={
                "agent_enabled": "true",
                "auth_user_role": "user",
                "memory_write_enabled": "false",
                "agent_required_tool": "deterministic_tool",
                "agent_required_tool_input": "fixture-input",
            },
        )

        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(tool.calls[0].arguments["payload"], "fixture-input")
        phases = event_phases(events)
        _assert_ordered_subsequence(
            self,
            phases,
            ["tool_selected", "decide", "tool_started", "tool_finished", "observe"],
        )

        finished_payload = first_phase_payload(events, "tool_finished")
        self.assertTrue(finished_payload["ok"])
        self.assertEqual(finished_payload["tool"], "deterministic_tool")
        self.assertIn('"answer":"fixed"', finished_payload["output"])
        self.assertIn('"answer":"fixed"', token_text(events))

        evaluation_payload = first_phase_payload(events, "evaluate")
        self.assertEqual(evaluation_payload["tool_success_rate"], 1.0)

    def test_tool_failure_is_bounded_and_traceable(self) -> None:
        tool = RecordingTool(
            name="controlled_failure_tool",
            ok=False,
            output="controlled failure",
            error_code="test_failure",
            details={"source": "characterization"},
        )
        runtime = _runtime_with_tools(tool)

        events = collect_runtime_events(
            runtime,
            prompt="exercise a controlled failing tool",
            metadata={
                "agent_enabled": "true",
                "auth_user_role": "user",
                "memory_write_enabled": "false",
                "agent_required_tool": "controlled_failure_tool",
                "agent_required_tool_input": "will-fail",
            },
        )

        self.assertEqual(len(tool.calls), 1)
        phases = event_phases(events)
        self.assertEqual(phases.count("tool_failed"), 1)
        self.assertEqual(phases[-1], "evaluate")

        failed_payload = first_phase_payload(events, "tool_failed")
        self.assertEqual(failed_payload["tool"], "controlled_failure_tool")
        self.assertEqual(failed_payload["error"]["code"], "test_failure")
        self.assertEqual(failed_payload["error"]["message"], "controlled failure")
        self.assertLessEqual(failed_payload["output"].count("controlled failure"), 1)
        self.assertLess(len(failed_payload["output_preview"]), 600)

        answer = token_text(events)
        self.assertIn("Mock assistant answer", answer)
        self.assertLessEqual(answer.count("controlled failure"), 2)
        self.assertLess(len(answer), 1200)

        evaluation_payload = first_phase_payload(events, "evaluate")
        self.assertEqual(evaluation_payload["tool_success_rate"], 0.0)

    def test_high_risk_tool_pauses_for_approval_without_completion(self) -> None:
        tool = RecordingTool(
            name="high_risk_echo",
            output="approved output: {input}",
            risk_level="high",
            requires_approval=True,
        )
        runtime = _runtime_with_tools(tool)

        service_events = asyncio.run(
            _collect_service_events(
                runtime,
                prompt="call the high risk fixture",
                metadata={
                    "agent_enabled": "true",
                    "auth_user_role": "admin",
                    "memory_write_enabled": "false",
                    "agent_required_tool": "high_risk_echo",
                    "agent_required_tool_input": "needs-approval",
                },
            )
        )

        event_types = [event.type for event in service_events]
        self.assertEqual(event_types.count(agent_pb2.AGENT_EVENT_TYPE_COMPLETED), 0)
        self.assertEqual(event_types.count(agent_pb2.AGENT_EVENT_TYPE_FAILED), 0)
        self.assertEqual(len(tool.calls), 0)

        approval_payload = _first_service_info_payload(service_events, "approval_required")
        self.assertEqual(approval_payload["tool"], "high_risk_echo")
        self.assertEqual(approval_payload["tool_name"], "high_risk_echo")
        self.assertEqual(approval_payload["tool_input"], "needs-approval")
        self.assertEqual(approval_payload["risk_level"], "high")
        self.assertEqual(approval_payload["resume_step_index"], 1)
        self.assertEqual(
            approval_payload["approved_tool_call"]["tool_input"],
            "needs-approval",
        )
        self.assertIn("approval", approval_payload["approval_reason"])

    def test_approval_resume_requires_exact_tool_call(self) -> None:
        broad_tool = RecordingTool(
            name="high_risk_echo",
            output="approved output: {input}",
            risk_level="high",
            requires_approval=True,
        )
        broad_runtime = _runtime_with_tools(broad_tool)
        broad_events = collect_runtime_events(
            broad_runtime,
            prompt="call high risk tool",
            metadata={
                "agent_enabled": "true",
                "auth_user_role": "admin",
                "approval_granted": "true",
                "memory_write_enabled": "false",
                "agent_required_tool": "high_risk_echo",
                "agent_required_tool_input": "approved-input",
            },
        )

        self.assertEqual(len(broad_tool.calls), 0)
        self.assertIn("approval_required", event_phases(broad_events))

        exact_tool = RecordingTool(
            name="high_risk_echo",
            output="approved output: {input}",
            risk_level="high",
            requires_approval=True,
        )
        exact_runtime = _runtime_with_tools(exact_tool)
        exact_events = collect_runtime_events(
            exact_runtime,
            prompt="call high risk tool",
            metadata={
                "agent_enabled": "true",
                "auth_user_role": "admin",
                "approval_granted": "true",
                "approved_tool_call": approved_tool_call(
                    "high_risk_echo",
                    "approved-input",
                    "high",
                    resume_step_index=1,
                ),
                "agent_resume_step_index": "1",
                "memory_write_enabled": "false",
                "agent_required_tool": "high_risk_echo",
                "agent_required_tool_input": "approved-input",
            },
        )

        self.assertEqual(len(exact_tool.calls), 1)
        self.assertNotIn("approval_required", event_phases(exact_events))
        self.assertIn("tool_finished", event_phases(exact_events))
        self.assertIn("approved output: approved-input", token_text(exact_events))

        mismatched_tool = RecordingTool(
            name="high_risk_echo",
            output="approved output: {input}",
            risk_level="high",
            requires_approval=True,
        )
        mismatched_runtime = _runtime_with_tools(mismatched_tool)
        mismatched_events = collect_runtime_events(
            mismatched_runtime,
            prompt="call high risk tool",
            metadata={
                "agent_enabled": "true",
                "auth_user_role": "admin",
                "approval_granted": "true",
                "approved_tool_call": approved_tool_call(
                    "high_risk_echo",
                    "different-input",
                    "high",
                    resume_step_index=1,
                ),
                "agent_resume_step_index": "1",
                "memory_write_enabled": "false",
                "agent_required_tool": "high_risk_echo",
                "agent_required_tool_input": "approved-input",
            },
        )

        self.assertEqual(len(mismatched_tool.calls), 0)
        self.assertIn("approval_required", event_phases(mismatched_events))

    def test_tool_policy_blocks_unauthorized_and_disabled_tools(self) -> None:
        unauthorized_tool = RecordingTool(
            name="admin_only_tool",
            output="should not execute",
        )
        unauthorized_runtime = _runtime_with_tools(
            unauthorized_tool,
            role_allow={"admin": {"*"}, "user": set()},
        )

        unauthorized_events = collect_runtime_events(
            unauthorized_runtime,
            prompt="call an admin only fixture",
            metadata={
                "agent_enabled": "true",
                "auth_user_role": "user",
                "memory_write_enabled": "false",
                "agent_required_tool": "admin_only_tool",
                "agent_required_tool_input": "blocked",
            },
        )

        self.assertEqual(len(unauthorized_tool.calls), 0)
        self.assertIn("policy_blocked", event_phases(unauthorized_events))
        blocked_payload = first_phase_payload(unauthorized_events, "policy_blocked")
        self.assertEqual(blocked_payload["tool"], "admin_only_tool")
        self.assertEqual(blocked_payload["role"], "user")

        disabled_tool = RecordingTool(
            name="disabled_tool",
            output="should not execute",
        )
        disabled_runtime = _runtime_with_tools(
            disabled_tool,
            disabled_tools={"disabled_tool"},
        )
        disabled_events = collect_runtime_events(
            disabled_runtime,
            prompt="call a disabled fixture",
            metadata={
                "agent_enabled": "true",
                "auth_user_role": "user",
                "memory_write_enabled": "false",
                "agent_required_tool": "disabled_tool",
                "agent_required_tool_input": "disabled",
            },
        )

        self.assertEqual(len(disabled_tool.calls), 0)
        self.assertIn("policy_blocked", event_phases(disabled_events))
        skipped_payload = first_phase_payload(disabled_events, "tool_skipped")
        self.assertEqual(skipped_payload["reason"], "policy_blocked")

    def test_memory_recall_write_toggle_and_user_isolation(self) -> None:
        memory_store = RecordingMemoryStore()
        memory_store.seed(
            user_id="alice",
            content="Gateway retry budget should remain bounded.",
            summary="bounded gateway retries",
            source_task_id="alice-seed",
            memory_id="alice-memory",
        )
        memory_store.seed(
            user_id="bob",
            content="Gateway retry budget should not leak across users.",
            summary="bounded gateway retries for bob",
            source_task_id="bob-seed",
            memory_id="bob-memory",
        )
        runtime = AgentRuntime(
            model_provider="mock",
            agent_memory_store=memory_store,
            agent_tool_audit_log_file="",
        )

        write_events = collect_runtime_events(
            runtime,
            prompt="recall gateway retries",
            task_id="alice-task",
            user_id="alice",
            metadata={
                "agent_enabled": "true",
                "memory_write_enabled": "true",
            },
        )

        self.assertEqual(memory_store.recall_calls[0][0], "alice")
        recall_payload = first_phase_payload(write_events, "memory_recall")
        self.assertEqual(recall_payload["hit_count"], 1)
        self.assertEqual(recall_payload["hits"][0]["source_task_id"], "alice-seed")
        self.assertEqual(len(memory_store.write_calls), 1)
        self.assertEqual(memory_store.write_calls[0].user_id, "alice")
        self.assertEqual(memory_store.write_calls[0].source_task_id, "alice-task")
        self.assertIn("memory_write", event_phases(write_events))

        no_write_events = collect_runtime_events(
            runtime,
            prompt="recall gateway retries",
            task_id="alice-no-write",
            user_id="alice",
            metadata={
                "agent_enabled": "true",
                "memory_write_enabled": "false",
            },
        )

        self.assertEqual(len(memory_store.write_calls), 1)
        self.assertNotIn("memory_write", event_phases(no_write_events))
        second_recall_payload = first_phase_payload(no_write_events, "memory_recall")
        recalled_sources = {
            hit["source_task_id"] for hit in second_recall_payload["hits"]
        }
        self.assertIn("alice-seed", recalled_sources)
        self.assertNotIn("bob-seed", recalled_sources)

        context_tool = RecordingTool(
            name="memory_context_tool",
            output="memory context observed",
        )
        context_runtime = AgentRuntime(
            model_provider="mock",
            agent_memory_store=memory_store,
            agent_tool_providers=[RecordingToolProvider([context_tool])],
            agent_tool_audit_log_file="",
        )
        collect_runtime_events(
            context_runtime,
            prompt="recall gateway retries",
            task_id="alice-context",
            user_id="alice",
            metadata={
                "agent_enabled": "true",
                "auth_user_role": "user",
                "memory_write_enabled": "false",
                "agent_required_tool": "memory_context_tool",
                "agent_required_tool_input": "inspect-memory",
            },
        )

        self.assertEqual(len(context_tool.contexts), 1)
        context_sources = {
            item["source_task_id"]
            for item in context_tool.contexts[0].recalled_memories
        }
        self.assertIn("alice-seed", context_sources)
        self.assertNotIn("bob-seed", context_sources)

    def test_first_token_timeout_returns_visible_fallback(self) -> None:
        runtime = FailingPromptRuntime(
            TimeoutError("model first token timeout after 0.1s")
        )

        events = collect_runtime_events(
            runtime,
            prompt="write a short answer without tools",
            metadata={
                "agent_enabled": "true",
                "memory_write_enabled": "false",
            },
        )

        self.assertIn("synthesis_failed", event_phases(events))
        self.assertEqual(len(runtime.prompt_calls), 2)
        self.assertIn("Model service is temporarily unavailable", token_text(events))
        self.assertEqual(event_phases(events)[-1], "evaluate")

    def test_runtime_accepts_injected_model_provider(self) -> None:
        provider = InjectedOpenAIProvider()
        runtime = AgentRuntime(
            model_provider=provider,
            model_provider_alias="gemini",
            agent_tool_audit_log_file="",
        )

        text = asyncio.run(_collect_prompt_text(runtime, "hello injected provider"))

        self.assertEqual(runtime.model_provider, "openai")
        self.assertEqual(runtime.model_provider_display, "gemini")
        self.assertEqual(text, "injected stream")
        self.assertEqual(
            provider.messages_seen[0][-1],
            {"role": "user", "content": "hello injected provider"},
        )

    def test_openai_empty_response_is_explicit(self) -> None:
        runtime = ScriptedOpenAIRuntime(
            rounds=[[RuntimeError("stream failed before first token")]],
            completion_results=[OpenAICompletionResult(content="", finish_reason="stop")],
        )

        text = asyncio.run(collect_openai_text(runtime, "short answer"))

        self.assertEqual(text, "(empty response)")
        self.assertEqual(len(runtime.completion_calls), 1)

    def test_openai_stream_interruption_continues_for_long_form(self) -> None:
        runtime = ScriptedOpenAIRuntime(
            rounds=[
                [
                    OpenAIStreamItem(content="Long answer begins with section one. "),
                    RuntimeError("socket closed"),
                ],
                [
                    OpenAIStreamItem(
                        content=f"Section two closes cleanly.{OPENAI_DONE_MARKER}"
                    ),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
            ],
            continuation_max_rounds=1,
            long_form_min_chars=10,
        )

        text = asyncio.run(
            collect_openai_text(runtime, "write a detailed report")
        )

        self.assertEqual(
            text,
            "Long answer begins with section one. Section two closes cleanly.",
        )
        self.assertEqual(len(runtime.calls), 2)
        self.assertIn(MODEL_MESSAGES_METADATA_KEY, runtime.calls[1][1])

    def test_openai_incomplete_markdown_fence_defers_done_marker(self) -> None:
        runtime = ScriptedOpenAIRuntime(
            rounds=[
                [
                    OpenAIStreamItem(
                        content=(
                            "Detailed example:\n```python\nprint('hello')"
                            f"{OPENAI_DONE_MARKER}"
                        )
                    ),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
                [
                    OpenAIStreamItem(
                        content=f"\n```\nConclusion.{OPENAI_DONE_MARKER}"
                    ),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
            ],
            continuation_max_rounds=1,
            long_form_min_chars=10,
        )

        text = asyncio.run(
            collect_openai_text(runtime, "write a detailed report")
        )

        self.assertNotIn(OPENAI_DONE_MARKER, text)
        self.assertEqual(text.count("```"), 2)
        self.assertTrue(text.rstrip().endswith("Conclusion."))
        self.assertEqual(len(runtime.calls), 2)

    def test_openai_continuation_trims_repeated_prefix(self) -> None:
        runtime = ScriptedOpenAIRuntime(
            rounds=[
                [
                    OpenAIStreamItem(
                        content=(
                            "This is a repeated continuation prefix that should stay once."
                        )
                    ),
                    OpenAIStreamItem(finish_reason="length"),
                ],
                [
                    OpenAIStreamItem(
                        content=(
                            "continuation prefix that should stay once. Then new section."
                        )
                    ),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
            ],
            continuation_max_rounds=1,
        )

        text = asyncio.run(collect_openai_text(runtime, "short answer"))

        self.assertEqual(
            text,
            "This is a repeated continuation prefix that should stay once. Then new section.",
        )
        self.assertEqual(text.count("continuation prefix that should stay once"), 1)

    def test_openai_completion_retries_http_429_and_500_without_network(self) -> None:
        for status in (429, 500):
            with self.subTest(status=status):
                runtime = AgentRuntime(
                    model_provider="openai",
                    openai_api_key="test-key",
                    openai_max_retries=2,
                    openai_retry_backoff_seconds=0.01,
                    agent_tool_audit_log_file="",
                )
                calls: list[object] = []

                def fake_urlopen(request: object, timeout: float) -> _JSONResponse:
                    calls.append((request, timeout))
                    if len(calls) == 1:
                        raise urllib_error.HTTPError(
                            url="https://example.invalid/chat/completions",
                            code=status,
                            msg="controlled failure",
                            hdrs={"Retry-After": "0.01"},
                            fp=io.BytesIO(b"controlled retry"),
                        )
                    return _JSONResponse(
                        {
                            "choices": [
                                {
                                    "finish_reason": "stop",
                                    "message": {
                                        "content": f"ok after HTTP {status}",
                                    },
                                }
                            ]
                        }
                    )

                with patch("app.providers.openai_compatible.urllib_request.urlopen", fake_urlopen), patch(
                    "app.providers.openai_compatible.time.sleep"
                ) as sleep_mock:
                    result = runtime._request_openai_completion_result("hello")

                self.assertEqual(result.content, f"ok after HTTP {status}")
                self.assertEqual(len(calls), 2)
                sleep_mock.assert_called_once()

    def test_service_failed_terminal_event_is_not_duplicated(self) -> None:
        runtime = ExplodingRuntime()

        service_events = asyncio.run(
            _collect_service_events(
                runtime,
                prompt="force runtime exception",
                metadata={"agent_enabled": "true"},
            )
        )

        event_types = [event.type for event in service_events]
        self.assertEqual(event_types.count(agent_pb2.AGENT_EVENT_TYPE_STARTED), 1)
        self.assertEqual(event_types.count(agent_pb2.AGENT_EVENT_TYPE_FAILED), 1)
        self.assertEqual(event_types.count(agent_pb2.AGENT_EVENT_TYPE_COMPLETED), 0)
        self.assertEqual(service_events[-1].message, "controlled runtime failure")


class ExplodingRuntime(AgentRuntime):
    def __init__(self) -> None:
        super().__init__(model_provider="mock", agent_tool_audit_log_file="")

    async def run_task(
        self,
        task_id: str,
        user_id: str,
        prompt: str,
        metadata: dict[str, str] | None = None,
    ):
        _ = (task_id, user_id, prompt, metadata)
        raise RuntimeError("controlled runtime failure")
        if False:
            yield RuntimeStreamItem(kind="token", token="")


class InjectedOpenAIProvider:
    provider_name = "openai"

    def __init__(self) -> None:
        self.messages_seen: list[list[dict[str, str]]] = []

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        long_form: bool = False,
    ):
        _ = long_form
        self.messages_seen.append(messages)
        yield OpenAIStreamItem(content="injected stream")

    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> OpenAICompletionResult:
        self.messages_seen.append(messages)
        return OpenAICompletionResult(content="injected completion")


class _JSONResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> "_JSONResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _runtime_with_tools(
    *tools: RecordingTool,
    role_allow: dict[str, set[str]] | None = None,
    disabled_tools: set[str] | None = None,
) -> AgentRuntime:
    return AgentRuntime(
        model_provider="mock",
        agent_tool_providers=[
            RecordingToolProvider(
                tools,
                role_allow=role_allow,
                disabled_tools=disabled_tools,
            )
        ],
        agent_tool_audit_log_file="",
    )


async def _collect_service_events(
    runtime: AgentRuntime,
    prompt: str,
    metadata: dict[str, str] | None = None,
) -> list[agent_pb2.AgentEvent]:
    service = AgentRuntimeService(runtime)
    request = agent_pb2.SubmitTaskRequest(
        task_id="service-characterization-task",
        user_id="service-characterization-user",
        prompt=prompt,
        metadata=metadata or {},
    )
    events: list[agent_pb2.AgentEvent] = []
    async for event in service.SubmitTask(request, None):
        events.append(event)
    return events


async def _collect_prompt_text(runtime: AgentRuntime, prompt: str) -> str:
    chunks: list[str] = []
    async for chunk in runtime.run_prompt(prompt):
        chunks.append(chunk)
    return "".join(chunks)


def _service_infos(events: list[agent_pb2.AgentEvent]) -> list[dict]:
    infos: list[dict] = []
    for event in events:
        if event.type != agent_pb2.AGENT_EVENT_TYPE_INFO or not event.message:
            continue
        try:
            infos.append(json.loads(event.message))
        except json.JSONDecodeError:
            continue
    return infos


def _service_info_phases(events: list[agent_pb2.AgentEvent]) -> list[str]:
    return [str(item.get("agent_event", "")) for item in _service_infos(events)]


def _first_service_info_payload(
    events: list[agent_pb2.AgentEvent],
    phase: str,
) -> dict:
    for info in _service_infos(events):
        if info.get("agent_event") == phase:
            payload = info.get("payload", {})
            if isinstance(payload, dict):
                return payload
    raise AssertionError(f"missing service info phase: {phase}")


def _assert_ordered_subsequence(
    test_case: unittest.TestCase,
    values: list[str],
    expected: list[str],
) -> None:
    cursor = 0
    for expected_value in expected:
        try:
            cursor = values.index(expected_value, cursor) + 1
        except ValueError:
            test_case.fail(
                f"missing {expected_value!r} after index {cursor}; observed {values!r}"
            )


if __name__ == "__main__":
    unittest.main()
