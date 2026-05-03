import asyncio
import json
import os
import pathlib
import statistics
import tempfile
import time
from dataclasses import dataclass
from typing import Any

from app.runtime import AgentRuntime


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    prompt: str
    metadata: dict[str, str]
    min_success: float
    expect_pause: bool
    required_events: tuple[str, ...]
    required_tools: tuple[str, ...]
    required_answer_contains: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkResult:
    case_id: str
    passed: bool
    paused: bool
    estimated_success: float
    tool_success_rate: float
    blocked_actions: int
    duration_ms: int
    missing_events: tuple[str, ...]
    missing_tools: tuple[str, ...]
    missing_answer_fragments: tuple[str, ...]


def _read_float_env(name: str, default_value: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default_value
    try:
        return float(raw)
    except ValueError:
        return default_value


def _load_cases(file_path: pathlib.Path) -> list[BenchmarkCase]:
    raw = file_path.read_text(encoding="utf-8")
    decoded = json.loads(raw)
    if not isinstance(decoded, list):
        raise RuntimeError("benchmark case file must be a JSON list")

    cases: list[BenchmarkCase] = []
    for item in decoded:
        if not isinstance(item, dict):
            continue

        case_id = str(item.get("id", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        if not case_id or not prompt:
            continue

        metadata_raw = item.get("metadata", {})
        metadata: dict[str, str] = {}
        if isinstance(metadata_raw, dict):
            for key, value in metadata_raw.items():
                if not isinstance(key, str):
                    continue
                metadata[key] = str(value)

        min_success = float(item.get("min_success", 0.6) or 0.6)
        expect_pause = bool(item.get("expect_pause", False))
        required_events = _read_string_tuple(item.get("required_events", []))
        required_tools = _read_string_tuple(item.get("required_tools", []))
        required_answer_contains = _read_string_tuple(item.get("required_answer_contains", []))

        cases.append(
            BenchmarkCase(
                case_id=case_id,
                prompt=prompt,
                metadata=metadata,
                min_success=min_success,
                expect_pause=expect_pause,
                required_events=required_events,
                required_tools=required_tools,
                required_answer_contains=required_answer_contains,
            )
        )

    return cases


def _read_string_tuple(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()

    values: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized:
            values.append(normalized)

    return tuple(values)


def _parse_agent_info(message: str) -> tuple[str, dict[str, Any]]:
    try:
        decoded = json.loads(message)
    except json.JSONDecodeError:
        return "", {}

    if not isinstance(decoded, dict):
        return "", {}

    phase = decoded.get("agent_event")
    payload = decoded.get("payload")
    if not isinstance(phase, str) or not isinstance(payload, dict):
        return "", {}

    return phase, payload


async def _run_case(runtime: AgentRuntime, case: BenchmarkCase, task_index: int) -> BenchmarkResult:
    metadata = dict(case.metadata)
    metadata.setdefault("agent_enabled", "true")

    started_at = time.time()
    paused = False
    estimated_success = 0.0
    tool_success_rate = 0.0
    blocked_actions = 0
    seen_events: set[str] = set()
    seen_tools: set[str] = set()
    answer_chunks: list[str] = []

    async for event in runtime.run_task(
        task_id=f"benchmark-{task_index}-{case.case_id}",
        user_id="benchmark-user",
        prompt=case.prompt,
        metadata=metadata,
    ):
        if event.kind == "pause":
            paused = True
            continue

        if event.kind == "token":
            answer_chunks.append(event.token)
            continue

        if event.kind != "info":
            continue

        phase, payload = _parse_agent_info(event.message)
        if phase:
            seen_events.add(phase)

        raw_tool = payload.get("tool")
        if isinstance(raw_tool, str) and raw_tool.strip():
            seen_tools.add(raw_tool.strip())

        if phase == "approval_required":
            paused = True

        if phase == "evaluate":
            estimated_success = float(payload.get("estimated_success", 0.0) or 0.0)
            tool_success_rate = float(payload.get("tool_success_rate", 0.0) or 0.0)
            blocked_actions = int(payload.get("blocked_actions", 0) or 0)

    duration_ms = int((time.time() - started_at) * 1000)

    if case.expect_pause:
        passed = paused
    else:
        passed = (not paused) and estimated_success >= case.min_success

    missing_events = tuple(item for item in case.required_events if item not in seen_events)
    missing_tools = tuple(item for item in case.required_tools if item not in seen_tools)
    answer_text = "".join(answer_chunks)
    missing_answer_fragments = tuple(
        item for item in case.required_answer_contains if item not in answer_text
    )
    passed = passed and not missing_events and not missing_tools and not missing_answer_fragments

    return BenchmarkResult(
        case_id=case.case_id,
        passed=passed,
        paused=paused,
        estimated_success=estimated_success,
        tool_success_rate=tool_success_rate,
        blocked_actions=blocked_actions,
        duration_ms=duration_ms,
        missing_events=missing_events,
        missing_tools=missing_tools,
        missing_answer_fragments=missing_answer_fragments,
    )


async def _run_all(cases: list[BenchmarkCase]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        memory_file = pathlib.Path(tmp_dir) / "regression-memory.json"
        runtime = AgentRuntime(
            model_provider="mock",
            agent_memory_file=str(memory_file),
            agent_enable_code_execution=True,
            agent_tool_http_allowlist=("example.com",),
            agent_tool_policy_json=os.getenv("SYNAPSE_AGENT_TOOL_POLICY_JSON", ""),
            agent_tool_audit_log_file="",
        )
        runtime.memory_write(
            user_id="benchmark-user",
            content="Gateway retries should be bounded and retryable upstream failures are audited.",
            summary="retry on retryable upstream failures",
            source_task_id="regression-seed-memory",
            importance=0.9,
        )

        results: list[BenchmarkResult] = []
        for index, case in enumerate(cases):
            result = await _run_case(runtime, case, index)
            results.append(result)

    total = len(results)
    passed_count = sum(1 for item in results if item.passed)
    success_rate = (passed_count / total) if total > 0 else 0.0

    tool_success_values = [item.tool_success_rate for item in results if not item.paused]
    avg_tool_success_rate = (
        statistics.mean(tool_success_values) if tool_success_values else 0.0
    )

    blocked_case_count = sum(1 for item in results if item.blocked_actions > 0)
    block_rate = (blocked_case_count / total) if total > 0 else 0.0

    duration_values = [item.duration_ms for item in results]
    avg_duration_ms = statistics.mean(duration_values) if duration_values else 0.0

    thresholds = {
        "min_success_rate": _read_float_env("SYNAPSE_AGENT_REGRESSION_MIN_SUCCESS_RATE", 0.8),
        "min_tool_success_rate": _read_float_env("SYNAPSE_AGENT_REGRESSION_MIN_TOOL_SUCCESS_RATE", 0.6),
        "max_block_rate": _read_float_env("SYNAPSE_AGENT_REGRESSION_MAX_BLOCK_RATE", 0.6),
        "max_avg_duration_ms": _read_float_env("SYNAPSE_AGENT_REGRESSION_MAX_AVG_DURATION_MS", 2000),
    }

    summary = {
        "total_cases": total,
        "passed_cases": passed_count,
        "success_rate": round(success_rate, 4),
        "avg_tool_success_rate": round(avg_tool_success_rate, 4),
        "block_rate": round(block_rate, 4),
        "avg_duration_ms": round(avg_duration_ms, 2),
        "thresholds": thresholds,
        "cases": [
            {
                "id": item.case_id,
                "passed": item.passed,
                "paused": item.paused,
                "estimated_success": round(item.estimated_success, 4),
                "tool_success_rate": round(item.tool_success_rate, 4),
                "blocked_actions": item.blocked_actions,
                "duration_ms": item.duration_ms,
                "missing_events": list(item.missing_events),
                "missing_tools": list(item.missing_tools),
                "missing_answer_fragments": list(item.missing_answer_fragments),
            }
            for item in results
        ],
    }

    failures: list[str] = []
    if success_rate < thresholds["min_success_rate"]:
        failures.append("success_rate")
    if avg_tool_success_rate < thresholds["min_tool_success_rate"]:
        failures.append("avg_tool_success_rate")
    if block_rate > thresholds["max_block_rate"]:
        failures.append("block_rate")
    if avg_duration_ms > thresholds["max_avg_duration_ms"]:
        failures.append("avg_duration_ms")

    summary["failed_metrics"] = failures
    summary["passed"] = len(failures) == 0

    return summary


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent
    cases_path = root / "cases.json"
    cases = _load_cases(cases_path)
    if not cases:
        raise RuntimeError("no benchmark cases found")

    summary = asyncio.run(_run_all(cases))
    print(json.dumps(summary, ensure_ascii=True, indent=2))

    return 0 if summary.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
