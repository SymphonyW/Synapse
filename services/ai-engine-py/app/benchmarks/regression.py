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
    tags: tuple[str, ...] = ()
    expect_memory_hit: bool = False
    expect_direct_answer: bool = False


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
    failure_reasons: tuple[str, ...]
    events: tuple[str, ...]
    tools: tuple[str, ...]
    event_counts: dict[str, int]
    tool_counts: dict[str, int]
    memory_hit_count: int
    approval_required_count: int
    direct_answer: bool
    answer_preview: str
    evaluation_seen: bool


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
        tags = _read_string_tuple(item.get("tags", []))
        expect_memory_hit = bool(item.get("expect_memory_hit", False))
        expect_direct_answer = bool(item.get("expect_direct_answer", False))

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
                tags=tags,
                expect_memory_hit=expect_memory_hit,
                expect_direct_answer=expect_direct_answer,
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

    # 每个 case 都完整记录事件、工具、回答片段和评估事件。
    # 回归失败时，调用方可以直接从 JSON 里定位缺失的是事件、工具还是回答内容。
    started_at = time.time()
    paused = False
    estimated_success = 0.0
    tool_success_rate = 0.0
    blocked_actions = 0
    seen_events: set[str] = set()
    seen_tools: set[str] = set()
    event_counts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    answer_chunks: list[str] = []
    memory_hit_count = 0
    approval_required_count = 0
    evaluation_seen = False

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
            event_counts[phase] = event_counts.get(phase, 0) + 1

        raw_tool = payload.get("tool")
        if isinstance(raw_tool, str) and raw_tool.strip():
            normalized_tool = raw_tool.strip()
            seen_tools.add(normalized_tool)
            tool_counts[normalized_tool] = tool_counts.get(normalized_tool, 0) + 1

        if phase == "approval_required":
            paused = True
            approval_required_count += 1

        if phase == "memory_recall":
            # memory_hit_count 只从 runtime 的标准事件读取，避免 benchmark
            # 私下访问 MemoryStore 后端导致评测和真实执行路径不一致。
            memory_hit_count = max(
                memory_hit_count,
                int(payload.get("hit_count", 0) or 0),
            )

        if phase == "evaluate":
            evaluation_seen = True
            estimated_success = float(payload.get("estimated_success", 0.0) or 0.0)
            tool_success_rate = float(payload.get("tool_success_rate", 0.0) or 0.0)
            blocked_actions = int(payload.get("blocked_actions", 0) or 0)

    duration_ms = int((time.time() - started_at) * 1000)

    missing_events = tuple(item for item in case.required_events if item not in seen_events)
    missing_tools = tuple(item for item in case.required_tools if item not in seen_tools)
    answer_text = "".join(answer_chunks)
    missing_answer_fragments = tuple(
        item for item in case.required_answer_contains if item not in answer_text
    )
    direct_answer = "tool_skipped" in seen_events and "tool_started" not in seen_events

    failure_reasons: list[str] = []
    if case.expect_pause:
        if not paused:
            failure_reasons.append("expected_pause_missing")
    else:
        if paused:
            failure_reasons.append("unexpected_pause")
        if not evaluation_seen:
            failure_reasons.append("evaluate_event_missing")
        elif estimated_success < case.min_success:
            failure_reasons.append(
                f"estimated_success_below_min:{estimated_success:.4f}<{case.min_success:.4f}"
            )

    for item in missing_events:
        failure_reasons.append(f"missing_event:{item}")
    for item in missing_tools:
        failure_reasons.append(f"missing_tool:{item}")
    for item in missing_answer_fragments:
        failure_reasons.append(f"missing_answer_fragment:{item}")

    if case.expect_memory_hit and memory_hit_count <= 0:
        failure_reasons.append("memory_hit_missing")
    if case.expect_direct_answer and not direct_answer:
        failure_reasons.append("direct_answer_missing")

    passed = len(failure_reasons) == 0

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
        failure_reasons=tuple(failure_reasons),
        events=tuple(sorted(seen_events)),
        tools=tuple(sorted(seen_tools)),
        event_counts=event_counts,
        tool_counts=tool_counts,
        memory_hit_count=memory_hit_count,
        approval_required_count=approval_required_count,
        direct_answer=direct_answer,
        answer_preview=_preview_text(answer_text, 360),
        evaluation_seen=evaluation_seen,
    )


def _preview_text(value: str, limit: int) -> str:
    normalized = " ".join(value.strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)] + "..."


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
        # 所有 memory 相关 case 都使用同一条种子记忆，确保 mock provider 下
        # 召回命中可重复，且不会依赖开发者本机的历史记忆文件。
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
    tool_success_rate = (
        statistics.mean(tool_success_values) if tool_success_values else 0.0
    )

    approval_expected = [
        result
        for case, result in zip(cases, results)
        if case.expect_pause
    ]
    approval_pause_rate = (
        sum(1 for item in approval_expected if item.paused) / len(approval_expected)
        if approval_expected
        else 0.0
    )

    memory_expected = [
        result
        for case, result in zip(cases, results)
        if case.expect_memory_hit
    ]
    memory_hit_rate = (
        sum(1 for item in memory_expected if item.memory_hit_count > 0) / len(memory_expected)
        if memory_expected
        else 0.0
    )

    blocked_case_count = sum(1 for item in results if item.blocked_actions > 0)
    block_rate = (blocked_case_count / total) if total > 0 else 0.0

    duration_values = [item.duration_ms for item in results]
    avg_duration_ms = statistics.mean(duration_values) if duration_values else 0.0

    thresholds = {
        "min_success_rate": _read_float_env("SYNAPSE_AGENT_REGRESSION_MIN_SUCCESS_RATE", 0.8),
        "min_tool_success_rate": _read_float_env("SYNAPSE_AGENT_REGRESSION_MIN_TOOL_SUCCESS_RATE", 0.6),
        "min_approval_pause_rate": _read_float_env(
            "SYNAPSE_AGENT_REGRESSION_MIN_APPROVAL_PAUSE_RATE", 1.0
        ),
        "min_memory_hit_rate": _read_float_env(
            "SYNAPSE_AGENT_REGRESSION_MIN_MEMORY_HIT_RATE", 1.0
        ),
        "max_block_rate": _read_float_env("SYNAPSE_AGENT_REGRESSION_MAX_BLOCK_RATE", 0.6),
        "max_avg_duration_ms": _read_float_env("SYNAPSE_AGENT_REGRESSION_MAX_AVG_DURATION_MS", 2000),
    }

    coverage = _build_tag_coverage(cases, results)

    summary = {
        "total_cases": total,
        "passed_cases": passed_count,
        "success_rate": round(success_rate, 4),
        "tool_success_rate": round(tool_success_rate, 4),
        "avg_tool_success_rate": round(tool_success_rate, 4),
        "approval_pause_rate": round(approval_pause_rate, 4),
        "memory_hit_rate": round(memory_hit_rate, 4),
        "block_rate": round(block_rate, 4),
        "avg_duration_ms": round(avg_duration_ms, 2),
        "approval_pause_expected_cases": len(approval_expected),
        "approval_pause_observed_cases": sum(1 for item in approval_expected if item.paused),
        "memory_expected_cases": len(memory_expected),
        "memory_hit_cases": sum(1 for item in memory_expected if item.memory_hit_count > 0),
        "coverage": coverage,
        "thresholds": thresholds,
        "cases": [
            {
                "id": item.case_id,
                "tags": list(case.tags),
                "passed": item.passed,
                "paused": item.paused,
                "estimated_success": round(item.estimated_success, 4),
                "tool_success_rate": round(item.tool_success_rate, 4),
                "blocked_actions": item.blocked_actions,
                "duration_ms": item.duration_ms,
                "failure_reasons": list(item.failure_reasons),
                "missing_events": list(item.missing_events),
                "missing_tools": list(item.missing_tools),
                "missing_answer_fragments": list(item.missing_answer_fragments),
                "events": list(item.events),
                "tools": list(item.tools),
                "event_counts": item.event_counts,
                "tool_counts": item.tool_counts,
                "memory_hit_count": item.memory_hit_count,
                "approval_required_count": item.approval_required_count,
                "direct_answer": item.direct_answer,
                "evaluation_seen": item.evaluation_seen,
                "answer_preview": item.answer_preview,
            }
            for case, item in zip(cases, results)
        ],
    }

    failures: list[str] = []
    if success_rate < thresholds["min_success_rate"]:
        failures.append("success_rate")
    if tool_success_rate < thresholds["min_tool_success_rate"]:
        failures.append("tool_success_rate")
    if approval_expected and approval_pause_rate < thresholds["min_approval_pause_rate"]:
        failures.append("approval_pause_rate")
    if memory_expected and memory_hit_rate < thresholds["min_memory_hit_rate"]:
        failures.append("memory_hit_rate")
    if block_rate > thresholds["max_block_rate"]:
        failures.append("block_rate")
    if avg_duration_ms > thresholds["max_avg_duration_ms"]:
        failures.append("avg_duration_ms")

    summary["failed_metrics"] = failures
    summary["passed"] = len(failures) == 0

    return summary


def _build_tag_coverage(
    cases: list[BenchmarkCase],
    results: list[BenchmarkResult],
) -> dict[str, dict[str, Any]]:
    # tags 用来表达“评测覆盖面”，不参与单 case 通过条件。
    # 后续新增能力时，只要给 case 标 tag，就能在汇总里看到该能力是否有红灯。
    coverage: dict[str, dict[str, Any]] = {}
    for case, result in zip(cases, results):
        for tag in case.tags:
            bucket = coverage.setdefault(
                tag,
                {
                    "total": 0,
                    "passed": 0,
                    "success_rate": 0.0,
                    "failed_cases": [],
                },
            )
            bucket["total"] += 1
            if result.passed:
                bucket["passed"] += 1
            else:
                bucket["failed_cases"].append(result.case_id)

    for bucket in coverage.values():
        total = int(bucket["total"])
        passed = int(bucket["passed"])
        bucket["success_rate"] = round((passed / total) if total > 0 else 0.0, 4)

    return coverage


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
