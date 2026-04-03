import json
import re
from dataclasses import dataclass
from typing import Callable

from app.tools.base import ToolContext, ToolResult
from app.tools.registry import ToolRegistry


class RetrievalTool:
    name = "retrieval"
    high_risk = False

    def execute(self, tool_input: str, context: ToolContext) -> ToolResult:
        if not context.recalled_memories:
            return ToolResult(ok=True, output="retrieval completed: no long-term memory matched")

        highlights: list[str] = []
        for item in context.recalled_memories[:3]:
            summary = str(item.get("summary", "")).strip()
            if summary:
                highlights.append(summary)
                continue

            preview = str(item.get("final_response_preview", "")).strip()
            if preview:
                highlights.append(preview)

        if not highlights:
            highlights = ["retrieval completed but records are empty"]

        return ToolResult(ok=True, output="retrieval hit: " + " | ".join(highlights))


@dataclass
class CalculatorTool:
    safe_eval: Callable[[str], str]

    name = "calculator"
    high_risk = False

    def execute(self, tool_input: str, context: ToolContext) -> ToolResult:
        expression = self._extract_expression(tool_input)
        if not expression:
            return ToolResult(ok=False, output="calculator failed: no expression found")

        try:
            value = self.safe_eval(expression)
        except Exception as exc:
            return ToolResult(ok=False, output=f"calculator failed: {exc}")

        return ToolResult(ok=True, output=f"calculator result: {value}")

    def _extract_expression(self, text: str) -> str:
        candidates = re.findall(r"[0-9\s\+\-\*\/%\(\)\.]{3,}", text)
        for raw in candidates:
            normalized = " ".join(raw.split())
            if not normalized:
                continue
            if not any(ch.isdigit() for ch in normalized):
                continue
            if not any(op in normalized for op in "+-*/%"):
                continue
            return normalized

        return ""


@dataclass
class BrowserFetchTool:
    fetch_http: Callable[[str, bool], ToolResult]

    name = "browser_fetch"
    high_risk = True

    def execute(self, tool_input: str, context: ToolContext) -> ToolResult:
        return self.fetch_http(tool_input, False)


@dataclass
class HttpAPITool:
    fetch_http: Callable[[str, bool], ToolResult]

    name = "http_api"
    high_risk = True

    def execute(self, tool_input: str, context: ToolContext) -> ToolResult:
        return self.fetch_http(tool_input, True)


@dataclass
class CodeExecTool:
    safe_eval: Callable[[str], str]
    enabled: bool

    name = "code_exec"
    high_risk = True

    def execute(self, tool_input: str, context: ToolContext) -> ToolResult:
        if not self.enabled:
            return ToolResult(
                ok=False,
                output="code_exec blocked: SYNAPSE_AGENT_ENABLE_CODE_EXECUTION is disabled",
            )

        expression = " ".join(tool_input.strip().split())
        if not expression:
            return ToolResult(ok=False, output="code_exec failed: empty expression")

        try:
            value = self.safe_eval(expression)
        except Exception as exc:
            return ToolResult(ok=False, output=f"code_exec failed: {exc}")

        return ToolResult(ok=True, output=f"code_exec result: {value}")


class JsonEchoTool:
    name = "json_echo"
    high_risk = False

    def execute(self, tool_input: str, context: ToolContext) -> ToolResult:
        payload = {
            "task_id": context.task_id,
            "user": context.user_id,
            "role": context.user_role,
            "tool_input": tool_input.strip(),
        }
        return ToolResult(ok=True, output="json_echo: " + json.dumps(payload, ensure_ascii=True))


def register_builtin_tools(
    registry: ToolRegistry,
    safe_eval: Callable[[str], str],
    fetch_http: Callable[[str, bool], ToolResult],
    enable_code_execution: bool,
) -> None:
    registry.register(RetrievalTool())
    registry.register(CalculatorTool(safe_eval=safe_eval))
    registry.register(BrowserFetchTool(fetch_http=fetch_http))
    registry.register(HttpAPITool(fetch_http=fetch_http))
    registry.register(CodeExecTool(safe_eval=safe_eval, enabled=enable_code_execution))
    registry.register(JsonEchoTool())
