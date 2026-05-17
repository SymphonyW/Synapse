import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.benchmarks.live_benchmark import (
    LiveBenchmarkCase,
    _filter_cases,
    _load_provider_config,
    _load_cases,
    _run_live_case,
)
from app.benchmarks.live_metrics import (
    LiveCaseResult,
    judge_answer,
    summarize_provider,
)
from app.benchmarks.live_report import (
    build_comparison_report,
    render_console_summary,
    render_markdown_report,
    write_reports,
)
from app.runtime import RuntimeStreamItem


def _info(phase: str, payload: dict | None = None) -> RuntimeStreamItem:
    return RuntimeStreamItem(
        kind="info",
        message=json.dumps(
            {
                "agent_event": phase,
                "payload": payload or {},
            }
        ),
    )


class ScriptedRuntime:
    def __init__(self, scripts: list[list[RuntimeStreamItem]]) -> None:
        self._scripts = [list(script) for script in scripts]
        self.calls: list[dict[str, str]] = []

    async def run_task(
        self,
        task_id: str,
        user_id: str,
        prompt: str,
        metadata: dict[str, str],
    ):
        _ = (task_id, user_id, prompt)
        self.calls.append(dict(metadata))
        script = self._scripts.pop(0)
        for item in script:
            yield item


def _case(**overrides) -> LiveBenchmarkCase:
    defaults = {
        "case_id": "calc",
        "title": "calculator",
        "prompt": "calculate 8 * 9",
        "metadata": {"agent_enabled": "true"},
        "tags": ("calculator",),
        "expectations": {
            "expected_final_status": "completed",
            "required_tools": ["calculator"],
        },
        "allowed_tools": ("calculator",),
        "timeout_seconds": 3,
        "judge_rules": {
            "required_keywords": ["72"],
            "required_conclusions": ["72"],
            "min_answer_chars": 2,
        },
    }
    defaults.update(overrides)
    return LiveBenchmarkCase(**defaults)


class LiveBenchmarkTests(unittest.TestCase):
    def test_load_cases_reads_live_schema_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "live_cases.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "calc",
                            "title": "Calculator",
                            "prompt": "calculate 8 * 9",
                            "metadata": {"agent_enabled": True},
                            "tags": ["calculator", "core"],
                            "expectations": {"required_tools": ["calculator"]},
                            "allowed_tools": ["calculator"],
                            "timeout_seconds": 12,
                            "judge_rules": {"required_keywords": ["72"]},
                        },
                        {
                            "id": "direct",
                            "title": "Direct answer",
                            "prompt": "answer directly",
                            "tags": ["direct_answer"],
                        },
                    ]
                ),
                encoding="utf-8",
            )

            cases = _load_cases(path)

        self.assertEqual(len(cases), 2)
        self.assertEqual(cases[0].metadata["agent_enabled"], "True")
        self.assertEqual(cases[0].allowed_tools, ("calculator",))
        self.assertEqual(cases[0].judge_rules["required_keywords"], ["72"])
        self.assertEqual(
            [case.case_id for case in _filter_cases(cases, {"calc"}, {"core"})],
            ["calc"],
        )
        self.assertEqual(
            [case.case_id for case in _filter_cases(cases, set(), {"direct_answer"})],
            ["direct"],
        )

    def test_rule_judge_scores_keywords_conclusions_empty_and_truncation(self) -> None:
        judged = judge_answer(
            "The final result is 72.",
            {
                "required_keywords": ["72"],
                "required_conclusions": ["final result"],
                "min_answer_chars": 10,
                "require_no_truncation": True,
            },
        )
        truncated = judge_answer(
            "This answer ends with and",
            {
                "required_keywords": ["answer"],
                "required_conclusions": ["answer"],
                "require_no_truncation": True,
            },
        )
        empty = judge_answer("", {"min_answer_chars": 1})

        self.assertTrue(judged.passed)
        self.assertEqual(judged.score, 1.0)
        self.assertFalse(judged.obvious_empty)
        self.assertFalse(judged.truncated)
        self.assertFalse(truncated.passed)
        self.assertTrue(truncated.truncated)
        self.assertIn("answer_truncated", truncated.failure_reasons)
        self.assertFalse(empty.passed)
        self.assertTrue(empty.obvious_empty)
        self.assertIn("answer_empty", empty.failure_reasons)

    def test_rule_judge_can_allow_empty_answer_for_expected_pause_cases(self) -> None:
        judged = judge_answer("", {"allow_empty_answer": True})

        self.assertTrue(judged.passed)
        self.assertTrue(judged.obvious_empty)

    def test_run_live_case_collects_task_tool_memory_and_quality_metrics(self) -> None:
        runtime = ScriptedRuntime(
            [
                [
                    _info("memory_recall", {"hit_count": 1}),
                    _info("tool_started", {"tool": "calculator"}),
                    _info("tool_finished", {"tool": "calculator"}),
                    _info("memory_write", {"memory_id": "m-1"}),
                    _info("evaluate", {"blocked_actions": 0}),
                    RuntimeStreamItem(kind="token", token="The final result is 72."),
                ]
            ]
        )

        result = asyncio.run(_run_live_case(runtime, _case(), 0))

        self.assertTrue(result.passed)
        self.assertTrue(result.completed_or_paused_correctly)
        self.assertEqual(result.final_status, "completed")
        self.assertTrue(result.required_tool_called)
        self.assertFalse(result.unexpected_tool_called)
        self.assertEqual(result.tool_call_count, 1)
        self.assertEqual(result.tool_success_rate, 1.0)
        self.assertEqual(result.tool_failure_count, 0)
        self.assertEqual(result.memory_recall_hit_count, 1)
        self.assertTrue(result.memory_write_happened)
        self.assertEqual(result.final_answer_chars, len("The final result is 72."))
        self.assertTrue(result.quality.passed)

    def test_run_live_case_can_resume_after_expected_pause(self) -> None:
        runtime = ScriptedRuntime(
            [
                [
                    _info(
                        "approval_required",
                        {
                            "tool": "summarize_page",
                            "approved_tool_call": {
                                "tool_name": "summarize_page",
                                "tool_input": "https://example.com",
                                "risk_level": "high",
                                "resume_step_index": 1,
                            },
                            "resume_step_index": 1,
                        },
                    ),
                    RuntimeStreamItem(kind="pause"),
                ],
                [
                    _info("resume_started", {"resume_step_index": 1}),
                    _info("tool_started", {"tool": "summarize_page"}),
                    _info("tool_finished", {"tool": "summarize_page"}),
                    _info("evaluate", {"blocked_actions": 0}),
                    RuntimeStreamItem(kind="token", token="Example Domain summary."),
                ],
            ]
        )
        case = _case(
            case_id="approval-resume",
            title="approval resume",
            prompt="visit https://example.com and summarize",
            tags=("approval_resume",),
            expectations={
                "expected_final_status": "completed",
                "expected_pause": True,
                "resume_after_pause": True,
                "required_tools": ["summarize_page"],
            },
            allowed_tools=("summarize_page",),
            judge_rules={"required_keywords": ["Example Domain"]},
        )

        result = asyncio.run(_run_live_case(runtime, case, 1))

        self.assertTrue(result.passed)
        self.assertEqual(result.final_status, "completed")
        self.assertTrue(result.expected_pause_matched)
        self.assertEqual(result.blocked_action_count, 1)
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(runtime.calls[1]["approval_granted"], "true")
        self.assertIn("summarize_page", runtime.calls[1]["approved_tool_call"])

    def test_run_live_case_flags_unexpected_tools_even_when_none_are_allowed(self) -> None:
        runtime = ScriptedRuntime(
            [
                [
                    _info("tool_started", {"tool": "calculator"}),
                    _info("tool_finished", {"tool": "calculator"}),
                    _info("evaluate", {"blocked_actions": 0}),
                    RuntimeStreamItem(kind="token", token="72."),
                ]
            ]
        )
        case = _case(
            case_id="direct",
            title="direct",
            prompt="answer directly",
            tags=("direct_answer",),
            expectations={"expected_final_status": "completed"},
            allowed_tools=(),
            judge_rules={"required_keywords": ["72"]},
        )

        result = asyncio.run(_run_live_case(runtime, case, 2))

        self.assertTrue(result.unexpected_tool_called)
        self.assertIn("unexpected_tool:calculator", result.failure_reasons)

    def test_provider_summary_and_comparison_cover_multi_provider_views(self) -> None:
        passing = LiveCaseResult(
            case_id="calc",
            title="Calculator",
            tags=("calculator",),
            passed=True,
            completed_or_paused_correctly=True,
            final_status="completed",
            latency_ms=100,
            total_events=5,
            final_answer_chars=20,
            required_tool_called=True,
            unexpected_tool_called=False,
            tool_call_count=1,
            tool_success_rate=1.0,
            tool_failure_count=0,
            replan_count=0,
            expected_pause_matched=True,
            blocked_action_count=0,
            memory_recall_hit_count=0,
            memory_write_happened=True,
            quality=judge_answer("result is 72.", {"required_keywords": ["72"]}),
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            estimated_cost_usd=None,
            failure_reasons=(),
            tools=("calculator",),
            events=("tool_finished", "evaluate"),
            answer_preview="result is 72.",
            attempt_count=1,
        )
        failing = LiveCaseResult(
            **{
                **passing.__dict__,
                "passed": False,
                "case_id": "browse",
                "title": "Browse",
                "tags": ("browser",),
                "latency_ms": 300,
                "tool_success_rate": 0.0,
                "tool_failure_count": 1,
                "failure_reasons": ("missing_keyword:Example Domain",),
            }
        )

        openai = summarize_provider("openai", [passing, failing])
        zhipu = summarize_provider("zhipu", [passing])
        comparison = build_comparison_report(
            {
                "openai": {"provider": "openai", "summary": openai, "cases": [passing.to_dict(), failing.to_dict()]},
                "zhipu": {"provider": "zhipu", "summary": zhipu, "cases": [passing.to_dict()]},
            }
        )

        self.assertEqual(openai["total_cases"], 2)
        self.assertEqual(openai["passed_cases"], 1)
        self.assertEqual(openai["replan_cases"], 0)
        self.assertEqual(comparison["providers"]["openai"]["failed_cases"], ["browse"])
        self.assertEqual(comparison["cases"]["calc"]["providers"]["openai"]["passed"], True)
        self.assertIn("calculator", comparison["tags"])

    def test_report_writers_emit_json_and_markdown(self) -> None:
        provider_report = {
            "provider": "openai",
            "config": {"model": "gpt-test"},
            "summary": {
                "total_cases": 1,
                "passed_cases": 1,
                "success_rate": 1.0,
                "avg_latency_ms": 120.0,
                "avg_tool_success_rate": 1.0,
                "pause_correctness_rate": 1.0,
                "replan_cases": 0,
                "failed_cases": [],
            },
            "cases": [],
        }
        comparison = build_comparison_report({"openai": provider_report})
        markdown = render_markdown_report(comparison)

        with tempfile.TemporaryDirectory() as tmp_dir:
            written = write_reports(
                Path(tmp_dir),
                {"openai": provider_report},
                comparison,
                include_markdown=True,
            )

            provider_json = Path(written["providers"]["openai"])
            comparison_json = Path(written["comparison_json"])
            markdown_path = Path(written["comparison_markdown"])

            self.assertTrue(provider_json.exists())
            self.assertTrue(comparison_json.exists())
            self.assertTrue(markdown_path.exists())
            self.assertIn("| provider | total cases |", markdown)
            self.assertIn("openai", markdown_path.read_text(encoding="utf-8"))

    def test_console_summary_makes_skipped_provider_explicit(self) -> None:
        provider_report = {
            "provider": "openai",
            "status": "skipped_invalid_config",
            "config_issues": ["api key is required"],
            "summary": {
                "total_cases": 0,
                "passed_cases": 0,
                "success_rate": 0.0,
                "avg_latency_ms": 0.0,
                "avg_tool_success_rate": 0.0,
                "pause_correctness_rate": 0.0,
                "replan_cases": 0,
                "failed_cases": [],
            },
            "cases": [],
        }

        rendered = render_console_summary(
            {"openai": provider_report},
            build_comparison_report({"openai": provider_report}),
        )

        self.assertIn("skipped_invalid_config", rendered)
        self.assertIn("api key is required", rendered)

    def test_provider_config_prefers_alias_specific_env_and_requires_non_openai_model(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "SYNAPSE_OPENAI_API_KEY": "generic-key",
                "SYNAPSE_OPENAI_BASE_URL": "https://generic.example/v1",
                "SYNAPSE_OPENAI_MODEL": "generic-model",
                "SYNAPSE_LIVE_BENCHMARK_ZHIPU_API_KEY": "zhipu-key",
                "SYNAPSE_LIVE_BENCHMARK_ZHIPU_BASE_URL": "https://zhipu.example/v1",
                "SYNAPSE_LIVE_BENCHMARK_ZHIPU_MODEL": "glm-4-air",
            },
            clear=True,
        ):
            zhipu = _load_provider_config("zhipu")

        with mock.patch.dict(os.environ, {}, clear=True):
            missing_model = _load_provider_config("gemini")

        self.assertEqual(zhipu.api_key, "zhipu-key")
        self.assertEqual(zhipu.base_url, "https://zhipu.example/v1")
        self.assertEqual(zhipu.model, "glm-4-air")
        self.assertIn("model is required", missing_model.issues())


if __name__ == "__main__":
    unittest.main()
