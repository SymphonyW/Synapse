from app.tools.base import AgentTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        self._tools[tool.name] = tool

    def get(self, tool_name: str) -> AgentTool | None:
        return self._tools.get(tool_name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools.keys()))
