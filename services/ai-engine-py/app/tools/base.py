from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolContext:
    task_id: str
    user_id: str
    user_role: str
    prompt: str
    metadata: dict[str, str]
    recalled_memories: list[dict[str, Any]]


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str


class AgentTool(Protocol):
    name: str
    high_risk: bool

    def execute(self, tool_input: str, context: ToolContext) -> ToolResult:
        ...
