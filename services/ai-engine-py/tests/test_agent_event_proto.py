import asyncio
import json
import unittest

from app.events import (
    AGENT_EVENT_V2_SCHEMA,
    AgentEventFactory,
    apply_typed_payload,
    serialize_legacy_info_event,
    to_proto_payload,
)
from app.runtime import RuntimeStreamItem
from app.service import AgentRuntimeService
from synapse.v1 import agent_pb2


class AgentEventProtoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = AgentEventFactory()

    def test_every_supported_domain_event_sets_expected_oneof(self) -> None:
        events = {
            "perceive": self.factory.perceive(
                task_id="task-1",
                short_context_count=2,
                recalled_memory_count=1,
            ),
            "memory_recall": self.factory.memory_recall(
                query="find prior task",
                hits=[
                    {
                        "memory_id": "mem-1",
                        "summary": "summary",
                        "content": "content",
                        "source_task_id": "task-0",
                        "importance": 0.8,
                        "score": 0.7,
                        "matched_terms": ["prior"],
                        "created_at": 123,
                    }
                ],
            ),
            "plan": self.factory.plan(steps=["inspect", "answer"]),
            "tool_selected": self.factory.tool_selected(_tool_payload()),
            "tool_started": self.factory.tool_started(_tool_payload()),
            "tool_finished": self.factory.tool_finished(
                {**_tool_payload(), "duration_ms": 9, "ok": True, "output": "fixed"}
            ),
            "tool_failed": self.factory.tool_failed(
                {
                    **_tool_payload(),
                    "duration_ms": 9,
                    "ok": False,
                    "output": "failed",
                    "error": {"code": "test_failure", "message": "controlled failure"},
                }
            ),
            "tool_skipped": self.factory.tool_skipped(
                {**_tool_payload(), "reason": "policy_blocked"}
            ),
            "approval_required": self.factory.approval_required(
                _tool_payload(),
                step_index=1,
                tool="deterministic_tool",
                tool_input="fixture-input",
                risk_level="high",
                reason="approval_required",
                approval_reason="high risk tool call requires approval",
                resume_step_index=1,
                approved_tool_call={
                    "tool_name": "deterministic_tool",
                    "tool_input": "fixture-input",
                    "risk_level": "high",
                    "reason": "high risk tool call requires approval",
                    "resume_step_index": 1,
                },
            ),
            "memory_write": self.factory.memory_write(
                memory_id="mem-2",
                user_id="user-1",
                summary="summary",
                content="content",
                source_task_id="task-1",
                importance=0.9,
                created_at=456,
            ),
            "evaluate": self.factory.evaluation(
                estimated_success=0.9,
                objective_completion=1.0,
                tool_success_rate=1.0,
                blocked_actions=0,
                duration_ms=123,
            ),
            "replan": self.factory.replan(
                step_index=1,
                reason="tool failed",
                from_tool="primary",
                to_tool="fallback",
                to_tool_input="same input",
            ),
            "synthesis_mode": self.factory.synthesis_mode(mode="planner"),
        }
        expected_oneofs = {
            "evaluate": "evaluation",
            **{event_name: event_name for event_name in events if event_name != "evaluate"},
        }

        for event_name, event in events.items():
            with self.subTest(event=event_name):
                proto_event = agent_pb2.AgentEvent(type=agent_pb2.AGENT_EVENT_TYPE_INFO)
                self.assertTrue(apply_typed_payload(proto_event, event))
                self.assertEqual(proto_event.schema_version, AGENT_EVENT_V2_SCHEMA)
                self.assertEqual(
                    proto_event.WhichOneof("typed_payload"),
                    expected_oneofs[event_name],
                )

    def test_tool_payload_keeps_legacy_and_typed_key_fields_consistent(self) -> None:
        event = self.factory.tool_failed(
            {
                **_tool_payload(),
                "duration_ms": 12,
                "ok": False,
                "output": "controlled failure output",
                "error": {
                    "code": "test_failure",
                    "message": "controlled failure",
                },
            }
        )
        legacy_payload = json.loads(serialize_legacy_info_event(event))["payload"]

        event_name, proto_payload = to_proto_payload(event)

        self.assertEqual(event_name, "tool_failed")
        self.assertEqual(proto_payload.step_index, legacy_payload["step_index"])
        self.assertEqual(proto_payload.tool_name, legacy_payload["tool"])
        self.assertEqual(proto_payload.tool_call_id, legacy_payload["tool_call_id"])
        self.assertEqual(proto_payload.input_preview, legacy_payload["tool_input"])
        self.assertEqual(proto_payload.output_preview, legacy_payload["output_preview"])
        self.assertEqual(proto_payload.error_code, legacy_payload["error"]["code"])
        self.assertEqual(proto_payload.error_message, legacy_payload["error"]["message"])
        self.assertFalse(proto_payload.ok)

    def test_approval_payload_matches_gateway_resume_contract(self) -> None:
        event = self.factory.approval_required(
            _tool_payload(),
            step_index=3,
            tool="browser_fetch",
            tool_input="https://example.com",
            risk_level="high",
            reason="approval_required",
            approval_reason="high risk tool call requires approval",
            resume_step_index=3,
            approved_tool_call={
                "tool_name": "browser_fetch",
                "tool_input": "https://example.com",
                "risk_level": "high",
                "reason": "high risk tool call requires approval",
                "resume_step_index": 3,
            },
        )

        _, proto_payload = to_proto_payload(event)

        self.assertEqual(proto_payload.step_index, 3)
        self.assertEqual(proto_payload.resume_step_index, 3)
        self.assertEqual(proto_payload.tool_name, "browser_fetch")
        self.assertEqual(proto_payload.tool_input, "https://example.com")
        self.assertEqual(proto_payload.risk_level, "high")
        self.assertEqual(proto_payload.tool_call_id, "call-1")
        self.assertEqual(proto_payload.approved_tool_call.tool_name, "browser_fetch")

    def test_service_info_event_double_writes_typed_and_legacy_payload(self) -> None:
        service = AgentRuntimeService(_FakeRuntime(self.factory.plan(steps=["inspect"])))
        request = agent_pb2.SubmitTaskRequest(
            task_id="task-1",
            user_id="user-1",
            prompt="inspect",
            metadata={"agent_enabled": "true"},
        )

        events = asyncio.run(_collect(service, request))
        info_events = [
            event for event in events if event.type == agent_pb2.AGENT_EVENT_TYPE_INFO
        ]

        self.assertEqual(len(info_events), 1)
        info = info_events[0]
        self.assertEqual(info.schema_version, AGENT_EVENT_V2_SCHEMA)
        self.assertEqual(info.WhichOneof("typed_payload"), "plan")
        self.assertEqual(info.plan.steps, ["inspect"])
        self.assertEqual(json.loads(info.message)["agent_event"], "plan")


class _FakeRuntime:
    model_provider_display = "mock"

    def __init__(self, info_event) -> None:
        self._message = serialize_legacy_info_event(info_event)

    async def run_task(self, task_id, user_id, prompt, metadata):
        _ = (task_id, user_id, prompt, metadata)
        yield RuntimeStreamItem(kind="info", message=self._message)


async def _collect(service: AgentRuntimeService, request: agent_pb2.SubmitTaskRequest):
    events = []
    async for event in service.SubmitTask(request, None):
        events.append(event)
    return events


def _tool_payload() -> dict:
    return {
        "step_index": 1,
        "objective": "use deterministic tool",
        "tool": "deterministic_tool",
        "tool_input": "fixture-input",
        "tool_call_id": "call-1",
        "risk_level": "low",
        "requires_approval": False,
        "tool_provider": "fixture",
        "tool_call": {
            "call_id": "call-1",
            "tool_name": "deterministic_tool",
            "input_text": "fixture-input",
            "arguments": {"payload": "fixture-input"},
        },
    }


if __name__ == "__main__":
    unittest.main()
