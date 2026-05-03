import json
import re
from dataclasses import dataclass
from typing import Any, Callable, ClassVar

from app.tools.base import BaseAgentTool, RiskLevel, ToolCall, ToolContext, ToolResult
from app.tools.registry import ToolRegistry


BrowserToolExecutor = Callable[[str, ToolCall, ToolContext], ToolResult]


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
                continue

            content = str(item.get("content", "")).strip()
            if content:
                highlights.append(content[:220])

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


class BrowserOperationTool(BaseAgentTool):
    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        operation: str,
        browse_web: BrowserToolExecutor,
        risk_level: RiskLevel = "high",
        requires_approval: bool = True,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.operation = operation
        self.browse_web = browse_web
        self.risk_level = risk_level
        self.requires_approval = requires_approval

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        # 浏览工具共享同一个 runtime 执行器，确保网络 allowlist、超时、大小限制和审计口径一致。
        # 具体工具只声明自身协议元数据，避免把安全边界分散在多个实现里。
        return self.browse_web(self.operation, call, context)


def _browser_tool_definitions(
    browse_web: BrowserToolExecutor,
) -> tuple[BrowserOperationTool, ...]:
    # search 先提供可测试的 URL 发现路径；后续接入搜索服务时仍然复用同一协议和网络护栏。
    search_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query or URL to discover browser sources.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    url_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "format": "uri",
                "description": "HTTP or HTTPS URL to browse.",
            }
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    citation_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "format": "uri",
                "description": "Source URL to cite.",
            },
            "title": {
                "type": "string",
                "description": "Optional source title.",
            },
            "snippet": {
                "type": "string",
                "description": "Optional source snippet.",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    return (
        BrowserOperationTool(
            name="search",
            description="Search for a browseable source and return candidate source URLs.",
            input_schema=search_schema,
            operation="search",
            browse_web=browse_web,
            risk_level="medium",
            requires_approval=False,
        ),
        BrowserOperationTool(
            name="open_url",
            description="Open an allowlisted URL and return page metadata plus a source URL.",
            input_schema=url_schema,
            operation="open_url",
            browse_web=browse_web,
        ),
        BrowserOperationTool(
            name="extract_text",
            description="Fetch an allowlisted page and extract readable text with source metadata.",
            input_schema=url_schema,
            operation="extract_text",
            browse_web=browse_web,
        ),
        BrowserOperationTool(
            name="summarize_page",
            description="Fetch an allowlisted page, extract text, and produce a short cited summary.",
            input_schema=url_schema,
            operation="summarize_page",
            browse_web=browse_web,
        ),
        BrowserOperationTool(
            name="source_citation",
            description="Format a source URL as a citation without performing a network request.",
            input_schema=citation_schema,
            operation="source_citation",
            browse_web=browse_web,
            risk_level="low",
            requires_approval=False,
        ),
    )


def _disabled_browser_tool(
    operation: str,
    call: ToolCall,
    context: ToolContext,
) -> ToolResult:
    # 单元测试可以只注册协议元数据而不提供网络执行器；此时浏览工具明确失败而不是静默联网。
    _ = (call, context)
    return ToolResult.failure(
        f"{operation} failed: browser execution is not configured",
        code="browser_disabled",
    )


def register_builtin_tools(
    registry: ToolRegistry,
    safe_eval: Callable[[str], str],
    fetch_http: Callable[[str, bool], ToolResult],
    enable_code_execution: bool,
    browse_web: BrowserToolExecutor | None = None,
) -> None:
    # 注册顺序不影响查找；把完整内置工具集集中在这里，
    # 让默认策略和协议测试共享同一个工具可用性来源。
    registry.register(RetrievalTool())
    registry.register(CalculatorTool(safe_eval=safe_eval))
    registry.register(BrowserFetchTool(fetch_http=fetch_http))
    registry.register(HttpAPITool(fetch_http=fetch_http))
    registry.register(CodeExecTool(safe_eval=safe_eval, enabled=enable_code_execution))
    registry.register(JsonEchoTool())
    for browser_tool in _browser_tool_definitions(browse_web or _disabled_browser_tool):
        registry.register(browser_tool)
