import json
import re
from dataclasses import dataclass
from typing import Any, Callable, ClassVar

from app.tools.base import BaseAgentTool, RiskLevel, ToolCall, ToolContext, ToolResult
from app.tools.registry import ToolRegistry


# 内置工具有意保持轻量：策略、审批和审计由 AgentRuntime 负责，
# 单个工具只关注校验调用形态并返回统一的 ToolResult。
class RetrievalTool(BaseAgentTool):
    name: ClassVar[str] = "retrieval"
    description: ClassVar[str] = "Read relevant long-term memory recalled for the current task."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The memory or context query to summarize.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    risk_level: ClassVar[RiskLevel] = "low"
    requires_approval: ClassVar[bool] = False

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        # retrieval 只总结 runtime 已召回的记忆，不自行读取存储。
        # 这样它保持低风险，记忆召回数量限制也只需在一个地方执行。
        if not context.recalled_memories:
            return ToolResult.success("retrieval completed: no long-term memory matched")

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

        return ToolResult.success("retrieval hit: " + " | ".join(highlights))


@dataclass
class CalculatorTool(BaseAgentTool):
    safe_eval: Callable[[str], str]

    name: ClassVar[str] = "calculator"
    description: ClassVar[str] = "Evaluate a simple arithmetic expression."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Arithmetic expression using numbers and + - * / % parentheses.",
            }
        },
        "required": ["expression"],
        "additionalProperties": False,
    }
    risk_level: ClassVar[RiskLevel] = "low"
    requires_approval: ClassVar[bool] = False

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        # 优先读取结构化 `expression` 参数，同时保留 `query` 和旧 input_text
        # 回退路径，保证旧 planner 输出仍可执行。
        tool_input = call.argument_text("expression", "query")
        expression = self._extract_expression(tool_input)
        if not expression:
            return ToolResult.failure(
                "calculator failed: no expression found",
                code="invalid_input",
            )

        try:
            value = self.safe_eval(expression)
        except Exception as exc:
            return ToolResult.failure(f"calculator failed: {exc}", code="execution_failed")

        return ToolResult.success(f"calculator result: {value}")

    def _extract_expression(self, text: str) -> str:
        # evaluator 由 runtime 注入。这里仅抽取像算式的文本，
        # 避免把任意自然语言传给安全求值器。
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
class BrowserFetchTool(BaseAgentTool):
    fetch_http: Callable[[str, bool], ToolResult]

    name: ClassVar[str] = "browser_fetch"
    description: ClassVar[str] = "Fetch a web page from an allowlisted HTTP or HTTPS host."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "format": "uri",
                "description": "HTTP or HTTPS URL to fetch.",
            }
        },
        "required": ["url"],
        "additionalProperties": False,
    }
    risk_level: ClassVar[RiskLevel] = "high"
    requires_approval: ClassVar[bool] = True

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        # HTTP 传输、allowlist 校验、重试和响应清理由 runtime 统一处理；
        # browser_fetch 与 http_api 共用同一策略边界，只在响应解析模式上不同。
        return self.fetch_http(call.argument_text("url"), False)


@dataclass
class HttpAPITool(BaseAgentTool):
    fetch_http: Callable[[str, bool], ToolResult]

    name: ClassVar[str] = "http_api"
    description: ClassVar[str] = "Fetch an allowlisted HTTP API endpoint and preserve JSON when possible."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "format": "uri",
                "description": "HTTP or HTTPS API URL to fetch.",
            }
        },
        "required": ["url"],
        "additionalProperties": False,
    }
    risk_level: ClassVar[RiskLevel] = "high"
    requires_approval: ClassVar[bool] = True

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        # parse_json=True 表示让共享 runtime fetcher 尽量保留 JSON payload，
        # 同时继续使用同一套网络访问护栏。
        return self.fetch_http(call.argument_text("url"), True)


@dataclass
class CodeExecTool(BaseAgentTool):
    safe_eval: Callable[[str], str]
    enabled: bool

    name: ClassVar[str] = "code_exec"
    description: ClassVar[str] = "Execute a restricted arithmetic expression through the runtime safe evaluator."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Restricted expression accepted by the safe evaluator.",
            }
        },
        "required": ["code"],
        "additionalProperties": False,
    }
    risk_level: ClassVar[RiskLevel] = "high"
    requires_approval: ClassVar[bool] = True

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        # 环境开关保留在工具内部作为最后防线。策略决定谁可以请求 code_exec；
        # 该开关决定当前 runtime 实例是否允许真正执行它。
        if not self.enabled:
            return ToolResult.failure(
                "code_exec blocked: SYNAPSE_AGENT_ENABLE_CODE_EXECUTION is disabled",
                code="tool_disabled",
            )

        tool_input = call.argument_text("code", "expression")
        # 当前实现仅限 runtime 安全求值器，不执行任意进程。
        # 后续扩展也应保持相同的 ToolResult/ToolError 契约。
        expression = " ".join(tool_input.strip().split())
        if not expression:
            return ToolResult.failure("code_exec failed: empty expression", code="invalid_input")

        try:
            value = self.safe_eval(expression)
        except Exception as exc:
            return ToolResult.failure(f"code_exec failed: {exc}", code="execution_failed")

        return ToolResult.success(f"code_exec result: {value}")


class JsonEchoTool(BaseAgentTool):
    name: ClassVar[str] = "json_echo"
    description: ClassVar[str] = "Echo the tool input and request identity as a JSON diagnostic payload."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "payload": {
                "type": "string",
                "description": "Text payload to echo.",
            }
        },
        "required": ["payload"],
        "additionalProperties": False,
    }
    risk_level: ClassVar[RiskLevel] = "low"
    requires_approval: ClassVar[bool] = False

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        # json_echo 是确定性的诊断工具，用于测试和回归用例验证结构化
        # ToolCall 参数经过 runtime 适配后仍被保留。
        payload = {
            "task_id": context.task_id,
            "user": context.user_id,
            "role": context.user_role,
            "tool_input": call.argument_text("payload"),
        }
        return ToolResult.success("json_echo: " + json.dumps(payload, ensure_ascii=True))


def register_builtin_tools(
    registry: ToolRegistry,
    safe_eval: Callable[[str], str],
    fetch_http: Callable[[str, bool], ToolResult],
    enable_code_execution: bool,
) -> None:
    # 注册顺序不影响查找；把完整内置工具集集中在这里，
    # 让默认策略和协议测试共享同一个工具可用性来源。
    registry.register(RetrievalTool())
    registry.register(CalculatorTool(safe_eval=safe_eval))
    registry.register(BrowserFetchTool(fetch_http=fetch_http))
    registry.register(HttpAPITool(fetch_http=fetch_http))
    registry.register(CodeExecTool(safe_eval=safe_eval, enabled=enable_code_execution))
    registry.register(JsonEchoTool())
