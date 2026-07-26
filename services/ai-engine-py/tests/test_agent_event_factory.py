import json
import unittest

from app.events import (
    AGENT_INFO_SCHEMA,
    LEGACY_AGENT_INFO_EVENT_TYPES,
    AgentEventFactory,
    AgentInfoEvent,
    ToolEventPayload,
    serialize_legacy_info_event,
)


class AgentEventFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = AgentEventFactory()

    def test_every_runtime_info_event_uses_stable_schema_and_legacy_name(self) -> None:
        tool_payload = {
            "step_index": 1,
            "objective": "use deterministic tool",
            "tool": "deterministic_tool",
            "tool_input": "fixed input",
            "risk_level": "low",
            "requires_approval": False,
            "duration_ms": 7,
            "ok": True,
            "output": "fixed output",
        }
        approval_record = {
            "tool_name": "browser_fetch",
            "tool_input": "https://example.com",
            "risk_level": "high",
            "reason": "test approval",
            "resume_step_index": 1,
        }

        events = [
            self.factory.perceive(
                task_id="task-1",
                short_context_count=2,
                recalled_memory_count=1,
            ),
            self.factory.memory_recall(
                query="recall gateway retries",
                hits=[
                    {
                        "memory_id": "mem-1",
                        "summary": "bounded retries",
                        "content": "Gateway retries are bounded.",
                        "source_task_id": "seed-task",
                        "importance": 0.9,
                        "created_at": 123,
                        "score": 0.8,
                        "matched_terms": ["gateway"],
                    }
                ],
            ),
            self.factory.plan(steps=["inspect", "answer"]),
            self.factory.resume_started(resume_step_index=2),
            self.factory.step_started(step_index=1, objective="inspect"),
            self.factory.tool_selected(tool_payload),
            self.factory.decide(
                step_index=1,
                tool="deterministic_tool",
                tool_input="fixed input",
                planner="mock",
                reason="forced",
            ),
            self.factory.tool_started(tool_payload),
            self.factory.tool_finished(tool_payload),
            self.factory.tool_failed({**tool_payload, "ok": False, "error": {"code": "test"}}),
            self.factory.tool_skipped({**tool_payload, "reason": "no_tool_selected"}),
            self.factory.policy_blocked(step_index=1, tool="code_exec", role="user"),
            self.factory.approval_required(
                step_index=1,
                tool="browser_fetch",
                tool_input="https://example.com",
                risk_level="high",
                reason="approval_required",
                approval_reason="high risk tool call requires approval",
                resume_step_index=1,
                approved_tool_call=approval_record,
            ),
            self.factory.paused(
                reason="tool browser_fetch requires explicit approval",
                tool="browser_fetch",
                resume_step_index=1,
            ),
            self.factory.observation(
                {
                    "step_index": 1,
                    "tool": "deterministic_tool",
                    "status": "finished",
                    "observation": "fixed output",
                }
            ),
            self.factory.reflection(step_index=1, reflection="done"),
            self.factory.replan(
                step_index=1,
                reason="tool failed",
                from_tool="primary",
                to_tool="fallback",
                to_tool_input="same input",
            ),
            self.factory.synthesis_mode(mode="planner"),
            self.factory.synthesis_failed(error="empty synthesis response"),
            self.factory.memory_write(
                memory_id="mem-2",
                user_id="alice",
                summary="summary",
                content="content",
                source_task_id="task-1",
                importance=0.7,
                created_at=456,
            ),
            self.factory.evaluation(
                estimated_success=0.9,
                objective_completion=1.0,
                tool_success_rate=1.0,
                blocked_actions=0,
                duration_ms=123,
            ),
            self.factory.diagnostic(message="diagnostic", payload={"detail": "visible"}),
        ]

        observed = {event.event_type for event in events}
        self.assertTrue(observed.issubset(LEGACY_AGENT_INFO_EVENT_TYPES))
        self.assertIn("act", observed)
        self.assertIn("observe", observed)
        self.assertIn("reflect", observed)
        self.assertIn("evaluate", observed)

        for event in events:
            with self.subTest(event=event.event_type):
                decoded = json.loads(serialize_legacy_info_event(event))
                self.assertEqual(decoded["schema"], AGENT_INFO_SCHEMA)
                self.assertEqual(decoded["agent_event"], event.event_type)
                self.assertIsInstance(decoded["payload"], dict)

    def test_serializer_keeps_legacy_envelope_and_unescaped_chinese(self) -> None:
        event = self.factory.info(
            "diagnostic",
            {"message": "中文事件", "nested": {"value": "工具完成"}},
            display_message="中文展示",
        )

        encoded = serialize_legacy_info_event(event)

        self.assertIn("中文事件", encoded)
        self.assertIn("中文展示", encoded)
        self.assertNotIn("\\u4e2d", encoded)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["schema"], AGENT_INFO_SCHEMA)
        self.assertEqual(decoded["agent_event"], "diagnostic")
        self.assertEqual(decoded["display_message"], "中文展示")
        self.assertEqual(decoded["payload"]["nested"]["value"], "工具完成")

    def test_preview_and_empty_values_are_normalized(self) -> None:
        long_output = "x" * 700

        event = self.factory.tool_finished(
            {
                "step_index": 0,
                "tool": "",
                "tool_input": None,
                "duration_ms": -25,
                "ok": True,
                "output": long_output,
            },
            display_message="",
        )
        decoded = json.loads(serialize_legacy_info_event(event))
        payload = decoded["payload"]

        self.assertEqual(payload["step_index"], 1)
        self.assertEqual(payload["tool"], "")
        self.assertEqual(payload["tool_input"], "")
        self.assertEqual(payload["duration_ms"], 0)
        self.assertEqual(len(payload["output_preview"]), 600)
        self.assertNotIn("display_message", decoded)

    def test_approval_required_payload_matches_gateway_resume_contract(self) -> None:
        approved_tool_call = {
            "tool_name": "summarize_page",
            "tool_input": "https://example.com",
            "risk_level": "high",
            "reason": "approval_required",
            "resume_step_index": 3,
        }

        event = self.factory.approval_required(
            {
                "objective": "summarize page",
                "tool_call": {
                    "call_id": "call-1",
                    "tool_name": "summarize_page",
                    "input_text": "https://example.com",
                    "arguments": {"url": "https://example.com"},
                },
            },
            step_index=3,
            tool="summarize_page",
            tool_input="https://example.com",
            risk_level="high",
            reason="approval_required",
            approval_reason="high risk tool call requires approval",
            resume_step_index=3,
            approved_tool_call=approved_tool_call,
        )
        payload = json.loads(serialize_legacy_info_event(event))["payload"]

        for key in (
            "step_index",
            "tool",
            "tool_name",
            "tool_input",
            "risk_level",
            "resume_step_index",
            "reason",
            "approval_reason",
            "approved_tool_call",
            "hint",
        ):
            self.assertIn(key, payload)

        self.assertEqual(payload["tool"], "summarize_page")
        self.assertEqual(payload["tool_name"], "summarize_page")
        self.assertEqual(payload["approved_tool_call"], approved_tool_call)

    def test_tool_event_payload_matches_web_trace_parser_contract(self) -> None:
        event = self.factory.tool_finished(
            {
                "step_index": 2,
                "objective": "calculate",
                "tool": "calculator",
                "tool_input": "8 * 9",
                "risk_level": "low",
                "duration_ms": 12,
                "ok": True,
                "output": "calculator result: 72",
                "requires_approval": False,
            }
        )
        payload = json.loads(serialize_legacy_info_event(event))["payload"]

        for key in (
            "step_index",
            "objective",
            "tool",
            "tool_input",
            "risk_level",
            "duration_ms",
            "ok",
            "output_preview",
            "requires_approval",
        ):
            self.assertIn(key, payload)

        self.assertEqual(payload["tool"], "calculator")
        self.assertEqual(payload["output_preview"], "calculator result: 72")

    def test_serializer_accepts_event_payload_dataclasses(self) -> None:
        event = AgentInfoEvent(
            event_type="tool_finished",
            payload=ToolEventPayload(
                step_index=1,
                tool="calculator",
                tool_input="1 + 1",
                ok=True,
                output="calculator result: 2",
            ),
        )

        decoded = json.loads(serialize_legacy_info_event(event))

        self.assertEqual(decoded["agent_event"], "tool_finished")
        self.assertEqual(decoded["payload"]["tool"], "calculator")
        self.assertTrue(decoded["payload"]["ok"])


if __name__ == "__main__":
    unittest.main()
