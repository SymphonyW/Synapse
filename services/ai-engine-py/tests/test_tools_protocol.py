import asyncio
import json
import unittest
from typing import Any

from app.runtime import AgentRuntime
from app.tools import ToolCall, ToolContext, ToolError, ToolRegistry, ToolResult
from app.tools.builtin import (
    CalculatorTool,
    CodeExecTool,
    JsonEchoTool,
    RetrievalTool,
    register_builtin_tools,
)


def _safe_eval(expression: str) -> str:
    # 测试只关注工具协议；生产 runtime 会提供真实安全求值器，
    # 这里的夹具只需要给出确定性的数学结果。
    return str(eval(expression, {"__builtins__": {}}, {}))


def _fetch_http(url: str, parse_json: bool) -> ToolResult:
    # 协议测试不发起网络请求；runtime fetcher 已通过回归用例覆盖集成路径。
    mode = "http_api" if parse_json else "browser_fetch"
    return ToolResult.success(f"{mode} response: {url}")


def _context() -> ToolContext:
    # 共享只读上下文，覆盖身份、角色、提示词元数据和召回记忆，
    # 同时不依赖文件型记忆存储。
    return ToolContext(
        task_id="task-1",
        user_id="user-1",
        user_role="user",
        prompt="protocol smoke test",
        metadata={},
        recalled_memories=[
            {
                "summary": "gateway retries are bounded",
                "final_response_preview": "unused",
            }
        ],
    )


async def _collect_runtime_infos(
    runtime: AgentRuntime,
    prompt: str,
    metadata: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    infos: list[dict[str, Any]] = []
    async for event in runtime.run_task(
        task_id="task-events",
        user_id="user-1",
        prompt=prompt,
        metadata=metadata or {},
    ):
        if event.kind == "info":
            infos.append(json.loads(event.message))
    return infos


def _agent_events(infos: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("agent_event", "")) for item in infos]


class ToolProtocolTests(unittest.TestCase):
    def test_builtin_tools_declare_protocol_metadata(self) -> None:
        # registry 是标准协议的执行点。该测试确保 planner 执行前，
        # 每个内置工具都能仅通过元数据被发现。
        registry = ToolRegistry()
        register_builtin_tools(
            registry,
            safe_eval=_safe_eval,
            fetch_http=_fetch_http,
            enable_code_execution=True,
        )

        self.assertEqual(
            registry.names(),
            (
                "browser_fetch",
                "calculator",
                "code_exec",
                "http_api",
                "json_echo",
                "retrieval",
            ),
        )

        for descriptor in registry.describe():
            self.assertIsInstance(descriptor["name"], str)
            self.assertIsInstance(descriptor["description"], str)
            self.assertTrue(descriptor["description"])
            self.assertIsInstance(descriptor["input_schema"], dict)
            self.assertIn(descriptor["risk_level"], {"low", "medium", "high", "critical"})
            self.assertIsInstance(descriptor["requires_approval"], bool)

        calculator_tool = registry.get("calculator")
        browser_fetch_tool = registry.get("browser_fetch")
        http_api_tool = registry.get("http_api")
        code_exec_tool = registry.get("code_exec")

        self.assertIsNotNone(calculator_tool)
        self.assertIsNotNone(browser_fetch_tool)
        self.assertIsNotNone(http_api_tool)
        self.assertIsNotNone(code_exec_tool)
        self.assertFalse(calculator_tool.requires_approval)
        self.assertTrue(browser_fetch_tool.requires_approval)
        self.assertTrue(http_api_tool.requires_approval)
        self.assertTrue(code_exec_tool.requires_approval)

    def test_tools_execute_with_tool_call(self) -> None:
        # 用结构化参数执行代表性的低风险工具，证明调用方不再需要
        # 向 execute() 传入裸字符串。
        context = _context()

        calculator = CalculatorTool(safe_eval=_safe_eval)
        calculator_result = calculator.execute(
            ToolCall(
                tool_name="calculator",
                arguments={"expression": "8 * 9"},
            ),
            context,
        )
        self.assertTrue(calculator_result.ok)
        self.assertEqual(calculator_result.output, "calculator result: 72")

        retrieval_result = RetrievalTool().execute(
            ToolCall(tool_name="retrieval", arguments={"query": "gateway retries"}),
            context,
        )
        self.assertTrue(retrieval_result.ok)
        self.assertIn("gateway retries are bounded", retrieval_result.output)

        echo_result = JsonEchoTool().execute(
            ToolCall(tool_name="json_echo", arguments={"payload": "hello protocol"}),
            context,
        )
        self.assertTrue(echo_result.ok)
        payload = json.loads(echo_result.output.removeprefix("json_echo: "))
        self.assertEqual(payload["tool_input"], "hello protocol")

    def test_tool_error_shape_is_returned_on_failure(self) -> None:
        # 失败结果既要通过 output 可读，也要通过 ToolError 机器可读，
        # 供后续策略和遥测逻辑使用。
        result = CodeExecTool(safe_eval=_safe_eval, enabled=False).execute(
            ToolCall(tool_name="code_exec", arguments={"code": "3 * 9"}),
            _context(),
        )

        self.assertFalse(result.ok)
        self.assertIsInstance(result.error, ToolError)
        self.assertEqual(result.error.code, "tool_disabled")
        self.assertFalse(result.error.retryable)

    def test_runtime_adapts_selected_tool_input_to_tool_call(self) -> None:
        # 现有 planner 仍以 (name, text) 形式选择工具。该测试锁定
        # 向后兼容的 runtime 适配器：调用具体工具前先把文本包装为 ToolCall。
        runtime = AgentRuntime(
            model_provider="mock",
            agent_enable_code_execution=True,
            agent_tool_audit_log_file="",
        )

        result = runtime._execute_tool(
            task_id="task-1",
            user_id="user-1",
            user_role="user",
            tool_name="calculator",
            tool_input="8 * 9",
            prompt="calculate 8 * 9",
            metadata={},
            recalled_memories=[],
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "calculator result: 72")

    def test_runtime_emits_standard_tool_success_events(self) -> None:
        # 标准工具事件是增量能力：旧 decide/observe 阶段继续存在，
        # 新 tool_* 阶段为 Gateway 和 UI 提供稳定标记。
        runtime = AgentRuntime(
            model_provider="mock",
            agent_enable_code_execution=True,
            agent_tool_audit_log_file="",
        )

        infos = asyncio.run(_collect_runtime_infos(runtime, "first calculate 8 * 9"))
        phases = _agent_events(infos)

        self.assertIn("tool_selected", phases)
        self.assertIn("tool_started", phases)
        self.assertIn("tool_finished", phases)
        self.assertIn("decide", phases)
        self.assertIn("observe", phases)

        tool_finished = next(item for item in infos if item["agent_event"] == "tool_finished")
        self.assertEqual(tool_finished["schema"], "synapse.agent.info.v1")
        self.assertEqual(tool_finished["payload"]["tool"], "calculator")
        self.assertTrue(tool_finished["payload"]["ok"])
        self.assertIn("display_message", tool_finished)

    def test_runtime_emits_standard_tool_skipped_event(self) -> None:
        # 不需要工具的步骤也会发出标准 tool_skipped 事件，
        # 便于客户端区分“有意跳过”和“缺少遥测”。
        runtime = AgentRuntime(model_provider="mock", agent_tool_audit_log_file="")

        infos = asyncio.run(_collect_runtime_infos(runtime, "summarize this note"))
        tool_skipped = next(item for item in infos if item["agent_event"] == "tool_skipped")

        self.assertEqual(tool_skipped["payload"]["reason"], "no_tool_selected")
        self.assertEqual(tool_skipped["payload"]["tool"], "none")

    def test_runtime_emits_standard_approval_required_event(self) -> None:
        # approval_required 保留 Gateway 暂停/恢复所需的旧 payload 字段，
        # 同时增加共享 schema 和可读展示文本。
        runtime = AgentRuntime(model_provider="mock", agent_tool_audit_log_file="")

        infos = asyncio.run(
            _collect_runtime_infos(
                runtime,
                "visit https://example.com and summarize",
                {"auth_user_role": "user", "approval_granted": "false"},
            )
        )
        approval = next(item for item in infos if item["agent_event"] == "approval_required")

        self.assertEqual(approval["schema"], "synapse.agent.info.v1")
        self.assertEqual(approval["payload"]["tool"], "browser_fetch")
        self.assertEqual(approval["payload"]["resume_step_index"], 1)
        self.assertEqual(approval["payload"]["reason"], "approval_required")

    def test_runtime_emits_standard_tool_failed_event(self) -> None:
        # 覆盖策略和审批均通过后的执行级失败：工具已被选择并启动，
        # 但由于当前 runtime 禁用代码执行而失败。
        runtime = AgentRuntime(
            model_provider="mock",
            agent_enable_code_execution=False,
            agent_tool_audit_log_file="",
        )

        infos = asyncio.run(
            _collect_runtime_infos(
                runtime,
                "run code 3 * 9",
                {"auth_user_role": "admin", "approval_granted": "true"},
            )
        )
        phases = _agent_events(infos)
        tool_failed = next(item for item in infos if item["agent_event"] == "tool_failed")

        self.assertIn("tool_started", phases)
        self.assertEqual(tool_failed["payload"]["tool"], "code_exec")
        self.assertFalse(tool_failed["payload"]["ok"])
        self.assertEqual(tool_failed["payload"]["error"]["code"], "tool_disabled")


if __name__ == "__main__":
    unittest.main()
