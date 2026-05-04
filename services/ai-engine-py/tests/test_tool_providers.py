import asyncio
import json
import unittest
from typing import Any

from app.runtime import AgentRuntime
from app.tools import (
    BaseAgentTool,
    LocalClassToolProvider,
    MCPToolProvider,
    OpenAPIToolProvider,
    ToolCall,
    ToolContext,
    ToolRegistry,
    ToolResult,
)


class LocalEchoTool(BaseAgentTool):
    name = "local_echo"
    description = "Echo text through a local Python tool."
    input_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to echo.",
            }
        },
        "required": ["text"],
        "additionalProperties": False,
    }
    risk_level = "low"
    requires_approval = False

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        # 本地 class 工具只关心标准 ToolCall；角色、审批和审计由 runtime 统一处理。
        _ = context
        return ToolResult.success(f"local_echo result: {call.argument_text('text')}")


class FakeMCPAdapter:
    def __init__(self) -> None:
        self.last_call: dict[str, Any] = {}

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "echo",
                "description": "Echo text from fake MCP adapter.",
                "input_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                "risk_level": "medium",
                "requires_approval": False,
            }
        ]

    def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        # fake adapter 模拟未来 MCP SDK；provider 只负责转发标准化后的参数。
        _ = context
        self.last_call = {"tool_name": tool_name, "arguments": dict(arguments)}
        return ToolResult.success(f"mcp result: {arguments.get('text', '')}")


def _context() -> ToolContext:
    return ToolContext(
        task_id="task-1",
        user_id="user-1",
        user_role="user",
        prompt="provider smoke test",
        metadata={},
        recalled_memories=[],
    )


async def _collect_runtime_infos(
    runtime: AgentRuntime,
    prompt: str,
    metadata: dict[str, str],
) -> list[dict[str, Any]]:
    infos: list[dict[str, Any]] = []
    async for event in runtime.run_task(
        task_id="provider-runtime",
        user_id="user-1",
        prompt=prompt,
        metadata=metadata,
    ):
        if event.kind == "info":
            infos.append(json.loads(event.message))
    return infos


class ToolProviderTests(unittest.TestCase):
    def test_local_class_provider_registers_schema_and_policy_defaults(self) -> None:
        registry = ToolRegistry()
        provider = LocalClassToolProvider(
            [LocalEchoTool],
            default_role_allow={"user": {"local_echo"}},
        )

        registered = registry.register_provider(provider)

        self.assertEqual(registered, ("local_echo",))
        self.assertEqual(registry.provider_for("local_echo"), "local_python")
        self.assertIn("local_echo", registry.schemas())
        self.assertEqual(
            registry.schemas()["local_echo"]["properties"]["text"]["type"],
            "string",
        )
        self.assertEqual(registry.default_role_allow()["user"], {"local_echo"})

        tool = registry.get("local_echo")
        self.assertIsNotNone(tool)
        result = tool.execute(
            ToolCall(tool_name="local_echo", arguments={"text": "hello"}),
            _context(),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "local_echo result: hello")

    def test_openapi_provider_discovers_schema_and_safe_default_executor(self) -> None:
        spec = {
            "paths": {
                "/tasks/{task_id}": {
                    "parameters": [
                        {
                            "name": "task_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "post": {
                        "operationId": "createTaskEvent",
                        "summary": "Create a task event",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"message": {"type": "string"}},
                                    }
                                }
                            },
                        },
                    },
                }
            }
        }
        registry = ToolRegistry()
        registry.register_provider(
            OpenAPIToolProvider(
                spec,
                default_role_allow={"admin": {"openapi_create_task_event"}},
            )
        )

        descriptor = next(item for item in registry.describe() if item["name"] == "openapi_create_task_event")
        self.assertEqual(descriptor["provider"], "openapi")
        self.assertEqual(descriptor["risk_level"], "high")
        self.assertTrue(descriptor["requires_approval"])

        schema = registry.schemas()["openapi_create_task_event"]
        self.assertEqual(schema["properties"]["task_id"]["type"], "string")
        self.assertIn("body", schema["required"])
        self.assertIn("task_id", schema["required"])
        self.assertEqual(
            registry.default_role_allow()["admin"],
            {"openapi_create_task_event"},
        )

        tool = registry.get("openapi_create_task_event")
        self.assertIsNotNone(tool)
        result = tool.execute(
            ToolCall(
                tool_name="openapi_create_task_event",
                arguments={"task_id": "task-1", "body": {"message": "hello"}},
            ),
            _context(),
        )
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "openapi_executor_missing")

    def test_mcp_provider_discovers_and_executes_adapter_tool(self) -> None:
        adapter = FakeMCPAdapter()
        registry = ToolRegistry()
        registry.register_provider(
            MCPToolProvider(
                adapter,
                default_role_allow={"user": {"mcp_echo"}},
            )
        )

        self.assertEqual(registry.provider_for("mcp_echo"), "mcp")
        tool = registry.get("mcp_echo")
        self.assertIsNotNone(tool)

        result = tool.execute(
            ToolCall(tool_name="mcp_echo", arguments={"text": "hello mcp"}),
            _context(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "mcp result: hello mcp")
        self.assertEqual(
            adapter.last_call,
            {"tool_name": "echo", "arguments": {"text": "hello mcp"}},
        )

    def test_runtime_uses_provider_policy_and_forced_tool_metadata(self) -> None:
        runtime = AgentRuntime(
            model_provider="mock",
            agent_tool_audit_log_file="",
            agent_tool_providers=(
                LocalClassToolProvider(
                    [LocalEchoTool],
                    default_role_allow={"user": {"local_echo"}},
                ),
            ),
        )

        infos = asyncio.run(
            _collect_runtime_infos(
                runtime,
                "use the local echo plugin",
                {
                    "agent_enabled": "true",
                    "auth_user_role": "user",
                    "agent_required_tool": "local_echo",
                    "agent_required_tool_input": "hello plugin",
                },
            )
        )

        phases = [item["agent_event"] for item in infos]
        self.assertIn("tool_finished", phases)
        self.assertNotIn("approval_required", phases)

        finished = next(item for item in infos if item["agent_event"] == "tool_finished")
        self.assertEqual(finished["payload"]["tool"], "local_echo")
        self.assertEqual(finished["payload"]["tool_provider"], "local_python")
        self.assertEqual(finished["payload"]["input_schema"]["required"], ["text"])
        self.assertEqual(finished["payload"]["output"], "local_echo result: hello plugin")


if __name__ == "__main__":
    unittest.main()
