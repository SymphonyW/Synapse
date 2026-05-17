import argparse
import asyncio
import json
import os
import pathlib
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable

from app.benchmarks.live_metrics import LiveCaseResult, build_live_case_result, summarize_provider
from app.benchmarks.live_report import (
    build_comparison_report,
    render_console_summary,
    write_reports,
)
from app.runtime import AgentRuntime


RuntimeFactory = Callable[["ProviderConfig", str], AgentRuntime]


@dataclass(frozen=True)
class LiveBenchmarkCase:
    case_id: str
    title: str
    prompt: str
    metadata: dict[str, str]
    tags: tuple[str, ...]
    expectations: dict[str, Any]
    allowed_tools: tuple[str, ...]
    timeout_seconds: int
    judge_rules: dict[str, Any]


@dataclass(frozen=True)
class ProviderConfig:
    alias: str
    runtime_provider: str
    base_url: str
    model: str
    api_key: str
    temperature: float
    max_tokens: int
    api_key_source: str
    base_url_source: str
    model_source: str

    def issues(self) -> list[str]:
        issues: list[str] = []
        if not self.alias:
            issues.append("provider alias is empty")
        if not self.model:
            issues.append("model is required")
        if not self.api_key:
            issues.append("api key is required")
        return issues

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "runtime_provider": self.runtime_provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_configured": bool(self.api_key),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "sources": {
                "api_key": self.api_key_source,
                "base_url": self.base_url_source,
                "model": self.model_source,
            },
        }


@dataclass
class _TraceState:
    event_counts: dict[str, int]
    tool_counts: dict[str, int]
    event_payloads: list[tuple[str, dict[str, Any]]]
    answer_chunks: list[str]
    total_events: int = 0
    observed_pause: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    last_approval_payload: dict[str, Any] | None = None

    @classmethod
    def empty(cls) -> "_TraceState":
        return cls(event_counts={}, tool_counts={}, event_payloads=[], answer_chunks=[])


def _load_cases(file_path: pathlib.Path) -> list[LiveBenchmarkCase]:
    decoded = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(decoded, list):
        raise RuntimeError("live benchmark case file must be a JSON list")

    cases: list[LiveBenchmarkCase] = []
    for item in decoded:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("id", "")).strip()
        title = str(item.get("title", case_id)).strip() or case_id
        prompt = str(item.get("prompt", "")).strip()
        if not case_id or not prompt:
            continue

        metadata_raw = item.get("metadata", {})
        metadata: dict[str, str] = {}
        if isinstance(metadata_raw, dict):
            for key, value in metadata_raw.items():
                if isinstance(key, str):
                    metadata[key] = str(value)

        cases.append(
            LiveBenchmarkCase(
                case_id=case_id,
                title=title,
                prompt=prompt,
                metadata=metadata,
                tags=_read_string_tuple(item.get("tags", [])),
                expectations=dict(item.get("expectations", {}) or {}),
                allowed_tools=_read_string_tuple(item.get("allowed_tools", [])),
                timeout_seconds=max(1, int(item.get("timeout_seconds", 90) or 90)),
                judge_rules=dict(item.get("judge_rules", {}) or {}),
            )
        )
    return cases


def _filter_cases(
    cases: list[LiveBenchmarkCase],
    case_ids: set[str],
    tags: set[str],
) -> list[LiveBenchmarkCase]:
    filtered = cases
    if case_ids:
        filtered = [item for item in filtered if item.case_id in case_ids]
    if tags:
        filtered = [item for item in filtered if tags.intersection(item.tags)]
    return filtered


async def _run_live_case(
    runtime: Any,
    case: LiveBenchmarkCase,
    task_index: int,
) -> LiveCaseResult:
    started_at = time.time()
    state = _TraceState.empty()
    task_id = f"live-benchmark-{task_index}-{case.case_id}"
    metadata = dict(case.metadata)
    metadata.setdefault("agent_enabled", "true")
    attempt_count = 0

    attempt_count += 1
    first_status = await _collect_attempt(
        runtime=runtime,
        task_id=task_id,
        prompt=case.prompt,
        metadata=metadata,
        timeout_seconds=case.timeout_seconds,
        state=state,
    )

    final_status = first_status
    if (
        first_status == "paused"
        and bool(case.expectations.get("resume_after_pause", False))
        and state.last_approval_payload is not None
    ):
        resume_metadata = _build_resume_metadata(metadata, state.last_approval_payload)
        attempt_count += 1
        final_status = await _collect_attempt(
            runtime=runtime,
            task_id=task_id,
            prompt=case.prompt,
            metadata=resume_metadata,
            timeout_seconds=case.timeout_seconds,
            state=state,
        )

    latency_ms = int((time.time() - started_at) * 1000)
    return build_live_case_result(
        case,
        event_counts=state.event_counts,
        tool_counts=state.tool_counts,
        event_payloads=state.event_payloads,
        observed_pause=state.observed_pause,
        final_status=final_status,
        latency_ms=latency_ms,
        total_events=state.total_events,
        answer_text="".join(state.answer_chunks),
        attempt_count=attempt_count,
        prompt_tokens=state.prompt_tokens,
        completion_tokens=state.completion_tokens,
        total_tokens=state.total_tokens,
    )


async def _collect_attempt(
    *,
    runtime: Any,
    task_id: str,
    prompt: str,
    metadata: dict[str, str],
    timeout_seconds: int,
    state: _TraceState,
) -> str:
    attempt_paused = False

    async def collect() -> None:
        nonlocal attempt_paused
        async for event in runtime.run_task(
            task_id=task_id,
            user_id="live-benchmark-user",
            prompt=prompt,
            metadata=metadata,
        ):
            state.total_events += 1
            if event.kind == "pause":
                attempt_paused = True
                state.observed_pause = True
                continue
            if event.kind == "token":
                state.answer_chunks.append(event.token)
                continue
            if event.kind != "info":
                continue

            phase, payload = _parse_agent_info(event.message)
            if not phase:
                continue
            state.event_counts[phase] = state.event_counts.get(phase, 0) + 1
            state.event_payloads.append((phase, payload))

            raw_tool = payload.get("tool")
            if isinstance(raw_tool, str) and raw_tool.strip():
                tool = raw_tool.strip()
                state.tool_counts[tool] = state.tool_counts.get(tool, 0) + 1

            if phase == "approval_required":
                attempt_paused = True
                state.observed_pause = True
                state.last_approval_payload = payload
            elif phase == "model_usage":
                state.prompt_tokens = _read_optional_int(payload.get("prompt_tokens"))
                state.completion_tokens = _read_optional_int(payload.get("completion_tokens"))
                state.total_tokens = _read_optional_int(payload.get("total_tokens"))

    try:
        await asyncio.wait_for(collect(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return "timeout"
    except Exception:
        return "failed"
    return "paused" if attempt_paused else "completed"


def _build_resume_metadata(
    metadata: dict[str, str],
    approval_payload: dict[str, Any],
) -> dict[str, str]:
    resumed = dict(metadata)
    resumed["approval_granted"] = "true"
    resumed["agent_resume_requested_by"] = "live-benchmark"
    approved_tool_call = approval_payload.get("approved_tool_call")
    if isinstance(approved_tool_call, dict):
        resumed["approved_tool_call"] = json.dumps(
            approved_tool_call,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    resume_step_index = approval_payload.get("resume_step_index")
    if resume_step_index is not None:
        resumed["agent_resume_step_index"] = str(resume_step_index)
    return resumed


async def _run_provider(
    config: ProviderConfig,
    cases: list[LiveBenchmarkCase],
    *,
    fail_fast: bool,
    runtime_factory: RuntimeFactory | None = None,
) -> dict[str, Any]:
    issues = config.issues()
    if issues:
        return {
            "provider": config.alias,
            "status": "skipped_invalid_config",
            "config": config.to_public_dict(),
            "config_issues": issues,
            "summary": summarize_provider(config.alias, []),
            "cases": [],
        }

    factory = runtime_factory or _build_runtime
    with tempfile.TemporaryDirectory() as tmp_dir:
        memory_file = str(pathlib.Path(tmp_dir) / f"{config.alias}-live-memory.json")
        runtime = factory(config, memory_file)
        _seed_live_memory(runtime)

        results: list[LiveCaseResult] = []
        for index, case in enumerate(cases):
            result = await _run_live_case(runtime, case, index)
            results.append(result)
            if fail_fast and not result.passed:
                break

    return {
        "provider": config.alias,
        "status": "completed",
        "config": config.to_public_dict(),
        "config_issues": [],
        "summary": summarize_provider(config.alias, results),
        "cases": [item.to_dict() for item in results],
    }


async def _run_providers(
    configs: list[ProviderConfig],
    cases: list[LiveBenchmarkCase],
    *,
    fail_fast: bool,
) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for config in configs:
        reports[config.alias] = await _run_provider(config, cases, fail_fast=fail_fast)
    return reports


def _build_runtime(config: ProviderConfig, memory_file: str) -> AgentRuntime:
    return AgentRuntime(
        model_provider=config.runtime_provider,
        model_provider_alias=config.alias if config.runtime_provider == "openai" else "",
        openai_api_key=config.api_key,
        openai_base_url=config.base_url,
        openai_model=config.model,
        openai_temperature=config.temperature,
        openai_max_tokens=config.max_tokens,
        agent_memory_file=memory_file,
        agent_enable_code_execution=True,
        agent_tool_http_allowlist=("example.com",),
        agent_tool_audit_log_file="",
    )


def _seed_live_memory(runtime: AgentRuntime) -> None:
    runtime.memory_write(
        user_id="live-benchmark-user",
        content="Gateway retries should be bounded and retryable upstream failures are audited.",
        summary="retry on retryable upstream failures",
        source_task_id="live-benchmark-seed-memory",
        importance=0.9,
    )


def _load_provider_config(
    alias: str,
    *,
    cli_base_url: str = "",
    cli_model: str = "",
    cli_api_key: str = "",
) -> ProviderConfig:
    normalized_alias = alias.strip().lower()
    env_prefix = f"SYNAPSE_LIVE_BENCHMARK_{normalized_alias.upper()}_"

    base_url, base_url_source = _resolve_value(
        cli_base_url,
        os.getenv(f"{env_prefix}BASE_URL", ""),
        os.getenv("SYNAPSE_OPENAI_BASE_URL", ""),
        "cli",
        f"{env_prefix}BASE_URL",
        "SYNAPSE_OPENAI_BASE_URL",
    )
    model, model_source = _resolve_value(
        cli_model,
        os.getenv(f"{env_prefix}MODEL", ""),
        os.getenv("SYNAPSE_OPENAI_MODEL", ""),
        "cli",
        f"{env_prefix}MODEL",
        "SYNAPSE_OPENAI_MODEL",
    )
    api_key, api_key_source = _resolve_value(
        cli_api_key,
        os.getenv(f"{env_prefix}API_KEY", ""),
        os.getenv("SYNAPSE_OPENAI_API_KEY", ""),
        "cli",
        f"{env_prefix}API_KEY",
        "SYNAPSE_OPENAI_API_KEY",
    )

    runtime_provider = normalized_alias if normalized_alias in {"gemini", "zhipu"} else "openai"
    resolved_model = model or ("gpt-4o-mini" if normalized_alias == "openai" else "")
    return ProviderConfig(
        alias=normalized_alias,
        runtime_provider=runtime_provider,
        base_url=base_url,
        model=resolved_model,
        api_key=api_key,
        temperature=_read_float(os.getenv("SYNAPSE_OPENAI_TEMPERATURE", "0.2"), 0.2),
        max_tokens=_read_int(os.getenv("SYNAPSE_OPENAI_MAX_TOKENS", "512"), 512),
        api_key_source=api_key_source,
        base_url_source=base_url_source,
        model_source=model_source,
    )


def _resolve_value(
    cli_value: str,
    specific_env_value: str,
    fallback_env_value: str,
    cli_source: str,
    specific_env_source: str,
    fallback_env_source: str,
) -> tuple[str, str]:
    if cli_value.strip():
        return cli_value.strip(), cli_source
    if specific_env_value.strip():
        return specific_env_value.strip(), specific_env_source
    if fallback_env_value.strip():
        return fallback_env_value.strip(), fallback_env_source
    return "", ""


def _parse_provider_aliases(args: argparse.Namespace) -> list[str]:
    raw = args.providers or args.provider or ""
    aliases = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return aliases or ["openai"]


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


def _read_string_tuple(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    values: list[str] = []
    for item in raw:
        normalized = str(item).strip()
        if normalized:
            values.append(normalized)
    return tuple(values)


def _read_optional_int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _read_float(raw: str, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _read_int(raw: str, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Synapse live provider benchmarks.")
    parser.add_argument("--provider", default="", help="Single provider alias, e.g. openai or zhipu.")
    parser.add_argument("--providers", default="", help="Comma-separated provider aliases.")
    parser.add_argument("--base-url", default="", help="Provider base URL override for single-provider runs.")
    parser.add_argument("--model", default="", help="Provider model override for single-provider runs.")
    parser.add_argument("--api-key", default="", help="Provider API key override for single-provider runs.")
    parser.add_argument("--case-id", action="append", default=[], help="Run only the selected case id.")
    parser.add_argument("--tag", action="append", default=[], help="Run cases matching at least one selected tag.")
    parser.add_argument(
        "--output",
        default="live-benchmark-output",
        help="Directory for JSON reports and optional Markdown output.",
    )
    parser.add_argument("--fail-fast", action="store_true", help="Stop a provider after its first failed case.")
    parser.add_argument(
        "--dry-run-config-check",
        action="store_true",
        help="Validate provider configuration without running any cases.",
    )
    parser.add_argument("--markdown", action="store_true", help="Also write a Markdown comparison report.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    aliases = _parse_provider_aliases(args)
    multi_provider = len(aliases) > 1
    if multi_provider and any((args.base_url, args.model, args.api_key)):
        parser.error("--base-url, --model and --api-key are only valid with a single provider")

    configs = [
        _load_provider_config(
            alias,
            cli_base_url=args.base_url if not multi_provider else "",
            cli_model=args.model if not multi_provider else "",
            cli_api_key=args.api_key if not multi_provider else "",
        )
        for alias in aliases
    ]

    if args.dry_run_config_check:
        payload = [
            {
                **config.to_public_dict(),
                "valid": len(config.issues()) == 0,
                "issues": config.issues(),
            }
            for config in configs
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if all(item["valid"] for item in payload) else 2

    root = pathlib.Path(__file__).resolve().parent
    cases = _filter_cases(
        _load_cases(root / "live_cases.json"),
        set(args.case_id),
        set(args.tag),
    )
    if not cases:
        raise RuntimeError("no live benchmark cases found after filtering")

    provider_reports = asyncio.run(
        _run_providers(configs, cases, fail_fast=bool(args.fail_fast))
    )
    comparison = build_comparison_report(provider_reports)
    output_paths = write_reports(
        pathlib.Path(args.output),
        provider_reports,
        comparison,
        include_markdown=bool(args.markdown),
    )

    print(render_console_summary(provider_reports, comparison))
    print(json.dumps({"reports": output_paths}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
