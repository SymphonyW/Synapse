import statistics
from dataclasses import dataclass
from typing import Any


_TERMINAL_CHARS = (
    ".",
    "!",
    "?",
    ";",
    "。",
    "！",
    "？",
    "；",
    "…",
    "]",
    ")",
    "}",
    '"',
    "'",
)
_TRAILING_FRAGMENT_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "because",
    "but",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "under",
    "with",
}


@dataclass(frozen=True)
class RuleJudgeResult:
    passed: bool
    score: float
    obvious_empty: bool
    truncated: bool
    checks: dict[str, bool]
    failure_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score,
            "obvious_empty": self.obvious_empty,
            "truncated": self.truncated,
            "checks": dict(self.checks),
            "failure_reasons": list(self.failure_reasons),
        }


@dataclass(frozen=True)
class LiveCaseResult:
    case_id: str
    title: str
    tags: tuple[str, ...]
    passed: bool
    completed_or_paused_correctly: bool
    final_status: str
    latency_ms: int
    total_events: int
    final_answer_chars: int
    required_tool_called: bool
    unexpected_tool_called: bool
    tool_call_count: int
    tool_success_rate: float | None
    tool_failure_count: int
    replan_count: int
    expected_pause_matched: bool
    blocked_action_count: int
    memory_recall_hit_count: int
    memory_write_happened: bool
    quality: RuleJudgeResult
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    estimated_cost_usd: float | None
    failure_reasons: tuple[str, ...]
    tools: tuple[str, ...]
    events: tuple[str, ...]
    answer_preview: str
    attempt_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "title": self.title,
            "tags": list(self.tags),
            "passed": self.passed,
            "completed_or_paused_correctly": self.completed_or_paused_correctly,
            "final_status": self.final_status,
            "latency_ms": self.latency_ms,
            "total_events": self.total_events,
            "final_answer_chars": self.final_answer_chars,
            "required_tool_called": self.required_tool_called,
            "unexpected_tool_called": self.unexpected_tool_called,
            "tool_call_count": self.tool_call_count,
            "tool_success_rate": self.tool_success_rate,
            "tool_failure_count": self.tool_failure_count,
            "replan_count": self.replan_count,
            "expected_pause_matched": self.expected_pause_matched,
            "blocked_action_count": self.blocked_action_count,
            "memory_recall_hit_count": self.memory_recall_hit_count,
            "memory_write_happened": self.memory_write_happened,
            "quality": self.quality.to_dict(),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "failure_reasons": list(self.failure_reasons),
            "tools": list(self.tools),
            "events": list(self.events),
            "answer_preview": self.answer_preview,
            "attempt_count": self.attempt_count,
        }


def judge_answer(answer_text: str, rules: dict[str, Any] | None = None) -> RuleJudgeResult:
    rules = dict(rules or {})
    normalized = answer_text.strip()
    lowered = normalized.lower()

    obvious_empty = normalized == "" or lowered in {"n/a", "none", "null"}
    allow_empty_answer = bool(rules.get("allow_empty_answer", False))
    truncated = _looks_truncated(normalized)

    checks: dict[str, bool] = {
        "answer_non_empty": (not obvious_empty) or allow_empty_answer,
    }
    failures: list[str] = []
    if obvious_empty and not allow_empty_answer:
        failures.append("answer_empty")

    for keyword in _read_string_list(rules.get("required_keywords")):
        ok = keyword.lower() in lowered
        checks[f"keyword:{keyword}"] = ok
        if not ok:
            failures.append(f"missing_keyword:{keyword}")

    for conclusion in _read_string_list(rules.get("required_conclusions")):
        ok = conclusion.lower() in lowered
        checks[f"conclusion:{conclusion}"] = ok
        if not ok:
            failures.append(f"missing_conclusion:{conclusion}")

    min_answer_chars = _read_optional_int(rules.get("min_answer_chars"))
    if min_answer_chars is not None:
        ok = len(normalized) >= min_answer_chars
        checks["min_answer_chars"] = ok
        if not ok:
            failures.append(f"answer_too_short:{len(normalized)}<{min_answer_chars}")

    max_answer_chars = _read_optional_int(rules.get("max_answer_chars"))
    if max_answer_chars is not None:
        ok = len(normalized) <= max_answer_chars
        checks["max_answer_chars"] = ok
        if not ok:
            failures.append(f"answer_too_long:{len(normalized)}>{max_answer_chars}")

    if bool(rules.get("require_no_truncation", False)):
        ok = not truncated
        checks["answer_not_truncated"] = ok
        if not ok:
            failures.append("answer_truncated")

    passed_checks = sum(1 for value in checks.values() if value)
    score = round((passed_checks / len(checks)) if checks else 1.0, 4)
    return RuleJudgeResult(
        passed=len(failures) == 0,
        score=score,
        obvious_empty=obvious_empty,
        truncated=truncated,
        checks=checks,
        failure_reasons=tuple(failures),
    )


def build_live_case_result(
    case: Any,
    *,
    event_counts: dict[str, int],
    tool_counts: dict[str, int],
    event_payloads: list[tuple[str, dict[str, Any]]],
    observed_pause: bool,
    final_status: str,
    latency_ms: int,
    total_events: int,
    answer_text: str,
    attempt_count: int,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
) -> LiveCaseResult:
    expectations = dict(getattr(case, "expectations", {}) or {})
    expected_status = str(expectations.get("expected_final_status", "completed")).strip() or "completed"
    expected_pause = bool(
        expectations.get("expected_pause", expected_status == "paused")
    )
    required_tools = tuple(_read_string_list(expectations.get("required_tools")))
    allowed_tools = tuple(getattr(case, "allowed_tools", ()) or ())

    seen_tools = tuple(sorted(tool_counts))
    completed_or_paused_correctly = final_status == expected_status
    required_tool_called = all(item in tool_counts for item in required_tools)
    unexpected_tools = tuple(item for item in seen_tools if item not in allowed_tools)
    unexpected_tool_called = len(unexpected_tools) > 0
    tool_call_count = int(event_counts.get("tool_started", 0))
    tool_failure_count = int(event_counts.get("tool_failed", 0))
    tool_success_count = int(event_counts.get("tool_finished", 0))
    tool_success_rate = (
        round(tool_success_count / tool_call_count, 4) if tool_call_count > 0 else None
    )
    replan_count = int(event_counts.get("replan", 0))
    expected_pause_matched = observed_pause == expected_pause
    blocked_action_count = int(event_counts.get("approval_required", 0)) + int(
        event_counts.get("policy_blocked", 0)
    )
    memory_recall_hit_count = _max_payload_int(event_payloads, "memory_recall", "hit_count")
    memory_write_happened = event_counts.get("memory_write", 0) > 0
    quality = judge_answer(answer_text, dict(getattr(case, "judge_rules", {}) or {}))

    failures: list[str] = []
    if not completed_or_paused_correctly:
        failures.append(f"unexpected_final_status:{final_status}!={expected_status}")
    if not expected_pause_matched:
        failures.append("expected_pause_mismatch")
    if not required_tool_called:
        missing = [item for item in required_tools if item not in tool_counts]
        failures.extend(f"missing_required_tool:{item}" for item in missing)
    if unexpected_tool_called:
        failures.extend(f"unexpected_tool:{item}" for item in unexpected_tools)

    min_replan_count = _read_optional_int(expectations.get("min_replan_count"))
    if min_replan_count is not None and replan_count < min_replan_count:
        failures.append(f"replan_count_below_min:{replan_count}<{min_replan_count}")

    min_tool_failure_count = _read_optional_int(expectations.get("min_tool_failure_count"))
    if min_tool_failure_count is not None and tool_failure_count < min_tool_failure_count:
        failures.append(
            f"tool_failure_count_below_min:{tool_failure_count}<{min_tool_failure_count}"
        )

    if bool(expectations.get("expect_memory_recall", False)) and memory_recall_hit_count <= 0:
        failures.append("memory_recall_missing")
    if bool(expectations.get("expect_memory_write", False)) and not memory_write_happened:
        failures.append("memory_write_missing")

    failures.extend(quality.failure_reasons)

    answer_preview = _preview_text(answer_text, 360)
    return LiveCaseResult(
        case_id=str(getattr(case, "case_id")),
        title=str(getattr(case, "title")),
        tags=tuple(getattr(case, "tags", ()) or ()),
        passed=len(failures) == 0,
        completed_or_paused_correctly=completed_or_paused_correctly,
        final_status=final_status,
        latency_ms=latency_ms,
        total_events=total_events,
        final_answer_chars=len(answer_text.strip()),
        required_tool_called=required_tool_called,
        unexpected_tool_called=unexpected_tool_called,
        tool_call_count=tool_call_count,
        tool_success_rate=tool_success_rate,
        tool_failure_count=tool_failure_count,
        replan_count=replan_count,
        expected_pause_matched=expected_pause_matched,
        blocked_action_count=blocked_action_count,
        memory_recall_hit_count=memory_recall_hit_count,
        memory_write_happened=memory_write_happened,
        quality=quality,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=None,
        failure_reasons=tuple(failures),
        tools=seen_tools,
        events=tuple(sorted(event_counts)),
        answer_preview=answer_preview,
        attempt_count=attempt_count,
    )


def summarize_provider(provider: str, results: list[LiveCaseResult]) -> dict[str, Any]:
    total_cases = len(results)
    passed_cases = sum(1 for item in results if item.passed)
    success_rate = round((passed_cases / total_cases) if total_cases else 0.0, 4)
    avg_latency_ms = round(
        statistics.mean(item.latency_ms for item in results) if results else 0.0,
        2,
    )
    tool_success_values = [
        item.tool_success_rate for item in results if item.tool_success_rate is not None
    ]
    avg_tool_success_rate = round(
        statistics.mean(tool_success_values) if tool_success_values else 0.0,
        4,
    )
    pause_correctness_rate = round(
        (
            sum(1 for item in results if item.expected_pause_matched) / total_cases
            if total_cases
            else 0.0
        ),
        4,
    )
    replan_cases = sum(1 for item in results if item.replan_count > 0)
    failed_cases = [item.case_id for item in results if not item.passed]
    prompt_tokens = _sum_optional_int(item.prompt_tokens for item in results)
    completion_tokens = _sum_optional_int(item.completion_tokens for item in results)
    total_tokens = _sum_optional_int(item.total_tokens for item in results)

    return {
        "provider": provider,
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "success_rate": success_rate,
        "avg_latency_ms": avg_latency_ms,
        "avg_tool_success_rate": avg_tool_success_rate,
        "pause_correctness_rate": pause_correctness_rate,
        "replan_cases": replan_cases,
        "failed_cases": failed_cases,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": None,
        "by_tag": summarize_tags(results),
    }


def summarize_tags(results: list[LiveCaseResult]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[LiveCaseResult]] = {}
    for result in results:
        for tag in result.tags:
            buckets.setdefault(tag, []).append(result)

    summary: dict[str, dict[str, Any]] = {}
    for tag, items in sorted(buckets.items()):
        total_cases = len(items)
        passed_cases = sum(1 for item in items if item.passed)
        latencies = [item.latency_ms for item in items]
        summary[tag] = {
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "success_rate": round((passed_cases / total_cases) if total_cases else 0.0, 4),
            "avg_latency_ms": round(statistics.mean(latencies) if latencies else 0.0, 2),
            "failed_cases": [item.case_id for item in items if not item.passed],
        }
    return summary


def _read_string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _read_optional_int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _looks_truncated(answer_text: str) -> bool:
    if not answer_text:
        return False
    if answer_text.count("```") % 2 == 1:
        return True
    if answer_text.endswith(_TERMINAL_CHARS):
        return False

    words = [
        item.lower()
        for item in answer_text[-80:].replace("\n", " ").split()
        if item.strip()
    ]
    return bool(words and words[-1].strip(",;:") in _TRAILING_FRAGMENT_WORDS)


def _preview_text(value: str, limit: int) -> str:
    normalized = " ".join(value.strip().split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)] + "..."


def _max_payload_int(
    event_payloads: list[tuple[str, dict[str, Any]]],
    phase: str,
    field_name: str,
) -> int:
    values: list[int] = []
    for item_phase, payload in event_payloads:
        if item_phase != phase:
            continue
        try:
            values.append(int(payload.get(field_name, 0) or 0))
        except (TypeError, ValueError):
            values.append(0)
    return max(values) if values else 0


def _sum_optional_int(values: Any) -> int | None:
    items = [item for item in values if item is not None]
    if not items:
        return None
    return sum(int(item) for item in items)
