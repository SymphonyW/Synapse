import asyncio
import json
import pathlib
import tempfile
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
    def __init__(self, risk_level: str = "medium", requires_approval: bool = False) -> None:
        self.last_call: dict[str, Any] = {}
        self.risk_level = risk_level
        self.requires_approval = requires_approval

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "echo",
                "description": "Echo text from fake MCP adapter.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                "risk_level": self.risk_level,
                "requires_approval": self.requires_approval,
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


def _agent_events(infos: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("agent_event", "")) for item in infos]


def _approved_tool_call(
    tool_name: str,
    tool_input: str,
    risk_level: str = "high",
    resume_step_index: int = 1,
) -> str:
    return json.dumps(
        {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "risk_level": risk_level,
            "reason": "unit test approval",
            "resume_step_index": resume_step_index,
        }
    )


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

    def test_openapi_provider_merges_parameters_and_uses_server_base_url(self) -> None:
        spec = {
            "servers": [{"url": "https://api.example.com/v1"}],
            "paths": {
                "/tasks/{task_id}": {
                    "parameters": [
                        {
                            "name": "task_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "verbose",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "boolean"},
                        },
                    ],
                    "get": {
                        "operationId": "getTask",
                        "parameters": [
                            {
                                "name": "verbose",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "X-Trace-Id",
                                "in": "header",
                                "schema": {"type": "string"},
                            },
                        ],
                    },
                }
            },
        }
        registry = ToolRegistry()
        registry.register_provider(OpenAPIToolProvider(spec))

        schema = registry.schemas()["openapi_get_task"]
        self.assertEqual(schema["properties"]["verbose"]["type"], "string")
        self.assertNotIn("verbose", schema.get("required", []))
        self.assertIn("task_id", schema["required"])
        self.assertEqual(schema["properties"]["X-Trace-Id"]["type"], "string")

        tool = registry.get("openapi_get_task")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.operation["base_url"], "https://api.example.com/v1")
        parameters = {
            (item["in"], item["name"]): item
            for item in tool.operation["parameters"]
        }
        self.assertEqual(parameters[("query", "verbose")]["schema"]["type"], "string")
        self.assertEqual(parameters[("path", "task_id")]["required"], True)

    def test_openapi_provider_registers_and_executes_injected_executor(self) -> None:
        captured: dict[str, Any] = {}

        def executor(
            operation: dict[str, Any],
            call: ToolCall,
            context: ToolContext,
        ) -> ToolResult:
            _ = context
            captured["operation"] = operation
            captured["arguments"] = dict(call.arguments)
            return ToolResult.success(f"{operation['method']} {operation['path']}")

        spec = {
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/items/{item_id}": {
                    "get": {
                        "operationId": "getItem",
                        "parameters": [
                            {"name": "item_id", "in": "path", "required": True},
                            {"name": "q", "in": "query"},
                        ],
                    }
                }
            },
        }
        registry = ToolRegistry()
        registry.register_provider(OpenAPIToolProvider(spec, executor=executor))
        tool = registry.get("openapi_get_item")
        self.assertIsNotNone(tool)

        result = tool.execute(
            ToolCall(
                tool_name="openapi_get_item",
                arguments={"item_id": "item-1", "q": "alpha"},
            ),
            _context(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "GET /items/{item_id}")
        self.assertEqual(captured["operation"]["base_url"], "https://api.example.com")
        self.assertEqual(captured["arguments"]["item_id"], "item-1")

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
        self.assertEqual(
            registry.schemas()["mcp_echo"]["properties"]["text"]["type"],
            "string",
        )
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

    def test_runtime_mcp_high_risk_tool_requires_approval(self) -> None:
        runtime = AgentRuntime(
            model_provider="mock",
            agent_tool_audit_log_file="",
            agent_tool_providers=(
                MCPToolProvider(FakeMCPAdapter(risk_level="high", requires_approval=True)),
            ),
        )

        infos = asyncio.run(
            _collect_runtime_infos(
                runtime,
                "use the mcp echo tool",
                {
                    "agent_enabled": "true",
                    "auth_user_role": "admin",
                    "agent_required_tool": "mcp_echo",
                    "agent_required_tool_input": "hello mcp",
                },
            )
        )

        phases = _agent_events(infos)
        self.assertIn("approval_required", phases)
        self.assertNotIn("tool_started", phases)
        approval = next(item for item in infos if item["agent_event"] == "approval_required")
        self.assertEqual(approval["payload"]["tool"], "mcp_echo")
        self.assertEqual(approval["payload"]["risk_level"], "high")

    def test_runtime_openapi_post_tool_requires_approval(self) -> None:
        spec = {
            "paths": {
                "/items": {
                    "post": {
                        "operationId": "createItem",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                    }
                                }
                            },
                        },
                    }
                }
            }
        }
        runtime = AgentRuntime(
            model_provider="mock",
            agent_tool_audit_log_file="",
            agent_tool_providers=(OpenAPIToolProvider(spec),),
        )

        infos = asyncio.run(
            _collect_runtime_infos(
                runtime,
                "create an item through openapi",
                {
                    "agent_enabled": "true",
                    "auth_user_role": "admin",
                    "agent_required_tool": "openapi_create_item",
                    "agent_required_tool_input": '{"body":{"name":"created"}}',
                },
            )
        )

        phases = _agent_events(infos)
        self.assertIn("approval_required", phases)
        self.assertNotIn("tool_started", phases)
        approval = next(item for item in infos if item["agent_event"] == "approval_required")
        self.assertEqual(approval["payload"]["tool"], "openapi_create_item")
        self.assertEqual(approval["payload"]["risk_level"], "high")

    def test_runtime_mcp_tool_audit_uses_governance_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audit_path = pathlib.Path(temp_dir) / "tool-audit.jsonl"
            adapter = FakeMCPAdapter(risk_level="high", requires_approval=True)
            runtime = AgentRuntime(
                model_provider="mock",
                agent_tool_audit_log_file=str(audit_path),
                agent_tool_providers=(MCPToolProvider(adapter),),
            )

            infos = asyncio.run(
                _collect_runtime_infos(
                    runtime,
                    "use the mcp echo tool",
                    {
                        "agent_enabled": "true",
                        "auth_user_role": "admin",
                        "approved_tool_call": _approved_tool_call("mcp_echo", "hello mcp"),
                        "agent_required_tool": "mcp_echo",
                        "agent_required_tool_input": "hello mcp",
                    },
                )
            )

            phases = _agent_events(infos)
            self.assertIn("tool_finished", phases)
            self.assertEqual(adapter.last_call["tool_name"], "echo")

            entries = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            mcp_entries = [entry for entry in entries if entry["tool"] == "mcp_echo"]
            actions = {entry["action"] for entry in mcp_entries}
            self.assertTrue({"approved", "executed"}.issubset(actions))
            executed = next(entry for entry in mcp_entries if entry["action"] == "executed")
            self.assertTrue(executed["ok"])
            self.assertEqual(executed["risk_level"], "high")


if __name__ == "__main__":
    unittest.main()
