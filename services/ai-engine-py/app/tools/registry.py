from app.tools.base import AgentTool
from app.tools.providers import ToolProvider, ToolProviderPolicy


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}
        self._tool_providers: dict[str, str] = {}
        self._provider_tools: dict[str, set[str]] = {}
        self._provider_policies: dict[str, ToolProviderPolicy] = {}

    def register(self, tool: AgentTool, provider_name: str = "local_python") -> None:
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

        normalized_provider = provider_name.strip().lower() or "local_python"
        self._tools[name] = tool
        self._tool_providers[name] = normalized_provider
        self._provider_tools.setdefault(normalized_provider, set()).add(name)

    def register_provider(self, provider: ToolProvider) -> tuple[str, ...]:
        # provider 负责发现工具，registry 负责逐个注册和校验 schema。
        # 返回注册成功的工具名，方便启动日志或测试确认发现结果。
        provider_name = provider.provider_name.strip().lower() or "provider"
        registered: list[str] = []
        for tool in provider.discover_tools():
            self.register(tool, provider_name=provider_name)
            registered.append(tool.name.strip().lower())

        self._provider_policies[provider_name] = provider.policy_defaults()
        return tuple(sorted(registered))

    def get(self, tool_name: str) -> AgentTool | None:
        # 工具名同时是策略和元数据标识，因此查找时使用与注册相同的标准化方式。
        return self._tools.get(tool_name.strip().lower())

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools.keys()))

    def provider_for(self, tool_name: str) -> str:
        return self._tool_providers.get(tool_name.strip().lower(), "")

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._provider_tools.keys()))

    def schemas(self) -> dict[str, dict[str, object]]:
        # schema registry 是只读快照，供 OpenAI tool choice、前端展示或治理层后续消费。
        return {
            name: dict(self._tools[name].input_schema)
            for name in self.names()
        }

    def describe(self) -> tuple[dict[str, object], ...]:
        # 只暴露稳定的声明字段；注入的回调、功能开关等 runtime 状态
        # 保持在具体工具实例内部。
        return tuple(
            {
                "name": tool.name,
                "provider": self.provider_for(name),
                "description": tool.description,
                "input_schema": tool.input_schema,
                "risk_level": tool.risk_level,
                "requires_approval": tool.requires_approval,
            }
            for name, tool in ((name, self._tools[name]) for name in self.names())
        )

    def default_role_allow(self) -> dict[str, set[str]]:
        merged: dict[str, set[str]] = {}
        for policy in self._provider_policies.values():
            for role, tools in policy.role_allow.items():
                normalized_role = str(role).strip().lower()
                if not normalized_role:
                    continue
                normalized_tools = {
                    str(tool).strip().lower()
                    for tool in tools
                    if str(tool).strip()
                }
                merged.setdefault(normalized_role, set()).update(normalized_tools)
        return merged

    def default_approval_required(self) -> set[str]:
        merged: set[str] = set()
        for policy in self._provider_policies.values():
            merged.update(
                str(tool).strip().lower()
                for tool in policy.approval_required
                if str(tool).strip()
            )
        return merged

    def default_disabled_tools(self) -> set[str]:
        merged: set[str] = set()
        for policy in self._provider_policies.values():
            merged.update(
                str(tool).strip().lower()
                for tool in policy.disabled_tools
                if str(tool).strip()
            )
        return merged
