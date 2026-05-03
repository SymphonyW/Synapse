from app.tools.base import AgentTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        # 在注册时校验公共协议，让不完整的自定义工具尽早失败，
        # 避免等到 planner 选择工具后才在任务执行深处报错。
        name = tool.name.strip().lower()
        if not name:
            raise ValueError("tool name is required")
        if not tool.description.strip():
            raise ValueError(f"tool {name} description is required")
        if not isinstance(tool.input_schema, dict) or not tool.input_schema:
            raise ValueError(f"tool {name} input_schema is required")
        if tool.risk_level not in {"low", "medium", "high", "critical"}:
            raise ValueError(f"tool {name} has unsupported risk_level: {tool.risk_level}")

        self._tools[name] = tool

    def get(self, tool_name: str) -> AgentTool | None:
        # 工具名同时是策略和元数据标识，因此查找时使用与注册相同的标准化方式。
        return self._tools.get(tool_name.strip().lower())

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools.keys()))

    def describe(self) -> tuple[dict[str, object], ...]:
        # 只暴露稳定的声明字段；注入的回调、功能开关等 runtime 状态
        # 保持在具体工具实例内部。
        return tuple(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "risk_level": tool.risk_level,
                "requires_approval": tool.requires_approval,
            }
            for tool in (self._tools[name] for name in self.names())
        )
