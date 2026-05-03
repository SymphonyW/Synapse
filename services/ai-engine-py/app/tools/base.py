from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


# 风险等级使用封闭枚举，避免策略默认值和 UI 展示去解析自由文本。
RiskLevel = Literal["low", "medium", "high", "critical"]


@dataclass(frozen=True)
class ToolContext:
    """所有工具共享的执行上下文。

    工具应把它视为只读请求状态。持久化副作用、网络访问和策略决策都放在
    该对象外部，保证工具协议可以独立测试。
    """

    task_id: str
    user_id: str
    user_role: str
    prompt: str
    metadata: dict[str, str]
    recalled_memories: list[dict[str, Any]]


@dataclass(frozen=True)
class ToolCall:
    """传给所有工具的标准调用信封。

    `input_text` 保留旧 runtime 中“工具选择返回纯字符串”的路径；
    `arguments` 是新调用方应使用的结构化参数。工具优先读取结构化键，
    再回退到 `input_text`，这样协议演进时不会破坏现有行为。
    """

    tool_name: str
    input_text: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""

    def argument_text(self, *keys: str) -> str:
        for key in keys:
            value = self.arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return self.input_text.strip()


@dataclass(frozen=True)
class ToolError:
    """附加在失败 ToolResult 上的机器可读错误。"""

    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """runtime 评分和审计日志统一消费的工具结果。

    `ok` 和 `output` 继续作为一等字段，兼容旧 tools 模块；`error` 和
    `metadata` 为后续调用方提供结构化细节，同时不改变现有 observation 文本。
    """

    ok: bool
    output: str
    error: ToolError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, output: str, metadata: dict[str, Any] | None = None) -> "ToolResult":
        return cls(ok=True, output=output, metadata=dict(metadata or {}))

    @classmethod
    def failure(
        cls,
        output: str,
        code: str = "tool_error",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> "ToolResult":
        return cls(
            ok=False,
            output=output,
            error=ToolError(
                code=code,
                message=output,
                retryable=retryable,
                details=dict(details or {}),
            ),
        )


class BaseAgentTool:
    """具体工具的便捷基类。

    具体工具覆盖静态协议字段和 `execute`。`high_risk` 属性作为兼容桥保留，
    供早于 `requires_approval` 的旧 runtime 逻辑继续识别高风险工具。
    """

    name = ""
    description = ""
    input_schema: dict[str, Any] = {"type": "string"}
    risk_level: RiskLevel = "low"
    requires_approval = False

    @property
    def high_risk(self) -> bool:
        return self.requires_approval or self.risk_level in {"high", "critical"}

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        raise NotImplementedError


class AgentTool(Protocol):
    """registry 使用的结构化协议。

    这样自定义工具不必继承 BaseAgentTool，也仍需暴露 runtime、策略层和测试
    依赖的公共字段。
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    risk_level: RiskLevel
    requires_approval: bool

    @property
    def high_risk(self) -> bool:
        ...

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        ...
