import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.benchmarks.regression import BenchmarkCase, _load_cases, _run_all, _run_case
from app.runtime import AgentRuntime


class BenchmarkRegressionTests(unittest.TestCase):
    def test_load_cases_reads_phase8_expectations(self) -> None:
        # case 文件是评测体系的外部契约；这里锁定第八阶段新增的
        # tags、memory/direct-answer 期望字段，避免后续改动静默丢失覆盖信息。
        with tempfile.TemporaryDirectory() as tmp_dir:
            cases_path = Path(tmp_dir) / "cases.json"
            cases_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "memory",
                            "prompt": "recall gateway retries",
                            "tags": ["memory_recall"],
                            "expect_memory_hit": True,
                            "expect_direct_answer": False,
                            "required_events": ["memory_recall"],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            cases = _load_cases(cases_path)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].tags, ("memory_recall",))
        self.assertTrue(cases[0].expect_memory_hit)
        self.assertFalse(cases[0].expect_direct_answer)
        self.assertEqual(cases[0].required_events, ("memory_recall",))

    def test_run_case_reports_failure_reasons(self) -> None:
        # 失败定位必须落到 case 级别；这里故意要求一个不存在的事件，
        # 验证 JSON 输出能够直接告诉调用方缺了哪个事件。
        runtime = AgentRuntime(
            model_provider="mock",
            agent_tool_audit_log_file="",
        )
        case = BenchmarkCase(
            case_id="missing-event",
            prompt="summarize this short product note in one sentence",
            metadata={"agent_enabled": "true"},
            min_success=0.0,
            expect_pause=False,
            required_events=("not_real_event",),
            required_tools=(),
            required_answer_contains=(),
        )

        result = asyncio.run(_run_case(runtime, case, 0))

        self.assertFalse(result.passed)
        self.assertIn("missing_event:not_real_event", result.failure_reasons)
        self.assertIn("tool_skipped", result.events)
        self.assertTrue(result.answer_preview)

    def test_run_all_outputs_phase8_summary_metrics(self) -> None:
        # 使用三个轻量 case 覆盖关键汇总指标：审批暂停率、记忆命中率、
        # mock 直答和平均耗时。网络类 case 由正式 cases.json regression 覆盖。
        cases = [
            BenchmarkCase(
                case_id="approval-pause",
                prompt="call external api at https://example.com/api and summarize",
                metadata={
                    "agent_enabled": "true",
                    "auth_user_role": "user",
                    "approval_granted": "false",
                },
                min_success=0.0,
                expect_pause=True,
                required_events=("approval_required",),
                required_tools=("http_api",),
                required_answer_contains=(),
                tags=("approval_pause",),
            ),
            BenchmarkCase(
                case_id="memory-hit",
                prompt="recall gateway retries and audited upstream failures from memory",
                metadata={
                    "agent_enabled": "true",
                    "auth_user_role": "user",
                    "approval_granted": "true",
                },
                min_success=0.65,
                expect_pause=False,
                required_events=("memory_recall", "tool_finished"),
                required_tools=("retrieval",),
                required_answer_contains=("retryable upstream failures",),
                tags=("memory_recall",),
                expect_memory_hit=True,
            ),
            BenchmarkCase(
                case_id="direct-answer",
                prompt="summarize this short product note in one sentence",
                metadata={
                    "agent_enabled": "true",
                    "auth_user_role": "user",
                    "approval_granted": "true",
                },
                min_success=0.7,
                expect_pause=False,
                required_events=("tool_skipped", "evaluate"),
                required_tools=(),
                required_answer_contains=("Mock assistant answer",),
                tags=("mock_direct_answer",),
                expect_direct_answer=True,
            ),
        ]
        env = {
            "SYNAPSE_AGENT_REGRESSION_MIN_SUCCESS_RATE": "1.0",
            "SYNAPSE_AGENT_REGRESSION_MIN_TOOL_SUCCESS_RATE": "0.0",
            "SYNAPSE_AGENT_REGRESSION_MIN_APPROVAL_PAUSE_RATE": "1.0",
            "SYNAPSE_AGENT_REGRESSION_MIN_MEMORY_HIT_RATE": "1.0",
            "SYNAPSE_AGENT_REGRESSION_MAX_BLOCK_RATE": "1.0",
            "SYNAPSE_AGENT_REGRESSION_MAX_AVG_DURATION_MS": "10000",
        }

        with mock.patch.dict(os.environ, env, clear=False):
            summary = asyncio.run(_run_all(cases))

        self.assertTrue(summary["passed"])
        self.assertEqual(summary["success_rate"], 1.0)
        self.assertIn("tool_success_rate", summary)
        self.assertEqual(summary["approval_pause_rate"], 1.0)
        self.assertEqual(summary["memory_hit_rate"], 1.0)
        self.assertIn("avg_duration_ms", summary)
        self.assertEqual(summary["memory_hit_cases"], 1)
        self.assertEqual(summary["approval_pause_observed_cases"], 1)
        self.assertEqual(summary["coverage"]["mock_direct_answer"]["success_rate"], 1.0)
        self.assertTrue(all(not item["failure_reasons"] for item in summary["cases"]))


if __name__ == "__main__":
    unittest.main()
