import json
from pathlib import Path
from typing import Any


def build_comparison_report(provider_reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    providers = {
        alias: dict(report.get("summary", {}))
        for alias, report in sorted(provider_reports.items())
    }
    cases: dict[str, dict[str, Any]] = {}
    tags: dict[str, dict[str, Any]] = {}

    for alias, report in sorted(provider_reports.items()):
        for case in report.get("cases", []):
            case_id = str(case.get("id", "")).strip()
            if not case_id:
                continue
            bucket = cases.setdefault(
                case_id,
                {
                    "title": str(case.get("title", "")),
                    "tags": list(case.get("tags", [])),
                    "providers": {},
                },
            )
            bucket["providers"][alias] = {
                "passed": bool(case.get("passed", False)),
                "final_status": case.get("final_status"),
                "latency_ms": case.get("latency_ms"),
                "tool_success_rate": case.get("tool_success_rate"),
                "replan_count": case.get("replan_count"),
                "failure_reasons": list(case.get("failure_reasons", [])),
            }

        for tag, tag_summary in report.get("summary", {}).get("by_tag", {}).items():
            tags.setdefault(tag, {})[alias] = dict(tag_summary)

    return {
        "providers": providers,
        "cases": cases,
        "tags": tags,
    }


def render_console_summary(
    provider_reports: dict[str, dict[str, Any]],
    comparison: dict[str, Any],
) -> str:
    _ = comparison
    lines = ["Live benchmark summary"]
    for alias, report in sorted(provider_reports.items()):
        status = str(report.get("status", "completed"))
        if status != "completed":
            issues = ", ".join(report.get("config_issues", [])) or "no details"
            lines.append(f"- {alias}: {status} ({issues})")
            continue
        summary = report.get("summary", {})
        lines.append(
            (
                f"- {alias}: {summary.get('passed_cases', 0)}/{summary.get('total_cases', 0)} "
                f"passed, success={summary.get('success_rate', 0.0):.4f}, "
                f"avg_latency_ms={summary.get('avg_latency_ms', 0.0):.2f}, "
                f"avg_tool_success={summary.get('avg_tool_success_rate', 0.0):.4f}, "
                f"pause_correctness={summary.get('pause_correctness_rate', 0.0):.4f}, "
                f"replan_cases={summary.get('replan_cases', 0)}, "
                f"failed_cases={summary.get('failed_cases', [])}"
            )
        )
    return "\n".join(lines)


def render_markdown_report(comparison: dict[str, Any]) -> str:
    lines = [
        "# Live Benchmark Report",
        "",
        "| provider | total cases | passed cases | success rate | avg latency ms | avg tool success | pause correctness | replan cases | failed cases |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for alias, summary in sorted(comparison.get("providers", {}).items()):
        failed_cases = ", ".join(summary.get("failed_cases", [])) or "-"
        lines.append(
            "| {alias} | {total} | {passed} | {success:.4f} | {latency:.2f} | {tool:.4f} | {pause:.4f} | {replan} | {failed} |".format(
                alias=alias,
                total=summary.get("total_cases", 0),
                passed=summary.get("passed_cases", 0),
                success=float(summary.get("success_rate", 0.0)),
                latency=float(summary.get("avg_latency_ms", 0.0)),
                tool=float(summary.get("avg_tool_success_rate", 0.0)),
                pause=float(summary.get("pause_correctness_rate", 0.0)),
                replan=summary.get("replan_cases", 0),
                failed=failed_cases,
            )
        )

    lines.extend(["", "## By case", ""])
    provider_aliases = list(sorted(comparison.get("providers", {})))
    header = "| case | tags | " + " | ".join(provider_aliases) + " |"
    separator = "|---|---|" + "|".join("---" for _ in provider_aliases) + "|"
    lines.extend([header, separator])
    for case_id, case in sorted(comparison.get("cases", {}).items()):
        cells = []
        for alias in provider_aliases:
            payload = case.get("providers", {}).get(alias)
            if payload is None:
                cells.append("-")
                continue
            mark = "✅" if payload.get("passed") else "❌"
            cells.append(
                f"{mark} {payload.get('final_status', '-')}, {payload.get('latency_ms', '-')} ms"
            )
        tags = ", ".join(case.get("tags", [])) or "-"
        lines.append(f"| {case_id} | {tags} | " + " | ".join(cells) + " |")

    lines.extend(["", "## By tag", ""])
    header = "| tag | " + " | ".join(provider_aliases) + " |"
    separator = "|---|" + "|".join("---" for _ in provider_aliases) + "|"
    lines.extend([header, separator])
    for tag, payload in sorted(comparison.get("tags", {}).items()):
        cells = []
        for alias in provider_aliases:
            summary = payload.get(alias)
            if summary is None:
                cells.append("-")
                continue
            cells.append(
                f"{summary.get('passed_cases', 0)}/{summary.get('total_cases', 0)} "
                f"({float(summary.get('success_rate', 0.0)):.4f})"
            )
        lines.append(f"| {tag} | " + " | ".join(cells) + " |")

    return "\n".join(lines) + "\n"


def write_reports(
    output_dir: Path,
    provider_reports: dict[str, dict[str, Any]],
    comparison: dict[str, Any],
    *,
    include_markdown: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Any] = {"providers": {}}
    for alias, report in sorted(provider_reports.items()):
        path = output_dir / f"{alias}.live-benchmark.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        written["providers"][alias] = str(path)

    comparison_json = output_dir / "comparison.live-benchmark.json"
    comparison_json.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    written["comparison_json"] = str(comparison_json)

    if include_markdown:
        comparison_markdown = output_dir / "comparison.live-benchmark.md"
        comparison_markdown.write_text(
            render_markdown_report(comparison),
            encoding="utf-8",
        )
        written["comparison_markdown"] = str(comparison_markdown)

    return written
