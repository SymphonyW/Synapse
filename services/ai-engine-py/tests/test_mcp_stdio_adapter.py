import pathlib
import sys
import tempfile
import textwrap
import unittest

from app.tools import ToolContext
from app.tools.mcp_stdio import StdioMCPAdapter


FAKE_MCP_SERVER = r"""
import json
import os
import sys
import time


MODE = os.environ.get("FAKE_MCP_MODE", "normal")


def emit(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def emit_error(message_id, code, message):
    emit(
        {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {"code": code, "message": message},
        }
    )


TOOLS = [
    {
        "name": "echo",
        "description": "Echo text from fake MCP server.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    }
]


for raw_line in sys.stdin:
    request = json.loads(raw_line)
    method = request.get("method")
    message_id = request.get("id")
    if message_id is None:
        continue

    if method == "initialize":
        if MODE == "initialize_error":
            emit_error(message_id, -32000, "handshake failed")
            continue
        emit(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-mcp", "version": "0.1"},
                },
            }
        )
        continue

    if method == "tools/list":
        if MODE == "list_error":
            emit_error(message_id, -32001, "list failed")
            continue
        emit({"jsonrpc": "2.0", "id": message_id, "result": {"tools": TOOLS}})
        continue

    if method == "tools/call":
        if MODE == "call_error":
            emit_error(message_id, -32002, "call failed")
            continue
        if MODE == "call_timeout":
            time.sleep(3)
            continue
        if MODE == "invalid_json_on_call":
            sys.stdout.write("{not-json\n")
            sys.stdout.flush()
            continue
        if MODE == "exit_on_call":
            sys.exit(3)
        params = request.get("params") or {}
        arguments = params.get("arguments") or {}
        emit(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "echo: " + str(arguments.get("text", "")),
                        }
                    ],
                    "isError": False,
                },
            }
        )
        continue

    emit_error(message_id, -32601, "unknown method")
"""


def _context() -> ToolContext:
    return ToolContext(
        task_id="task-1",
        user_id="user-1",
        user_role="admin",
        prompt="mcp stdio adapter smoke test",
        metadata={},
        recalled_memories=[],
    )


def _write_fake_server(temp_dir: pathlib.Path) -> pathlib.Path:
    script_path = temp_dir / "fake_mcp_server.py"
    script_path.write_text(textwrap.dedent(FAKE_MCP_SERVER), encoding="utf-8")
    return script_path


def _adapter(temp_dir: pathlib.Path, mode: str = "normal", timeout: float = 0.5) -> StdioMCPAdapter:
    script_path = _write_fake_server(temp_dir)
    return StdioMCPAdapter(
        command=sys.executable,
        args=("-u", str(script_path)),
        env={"FAKE_MCP_MODE": mode},
        working_dir=str(temp_dir),
        timeout_seconds=timeout,
    )


class StdioMCPAdapterTests(unittest.TestCase):
    def test_execute_tool_converts_start_failure_to_failure(self) -> None:
        adapter = StdioMCPAdapter(
            command="definitely-not-a-synapse-mcp-command",
            timeout_seconds=0.1,
        )

        result = adapter.execute_tool("echo", {"text": "hello"}, _context())

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "mcp_start_failed")

    def test_list_tools_discovers_remote_schema(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            adapter = _adapter(pathlib.Path(raw_temp_dir))
            try:
                tools = list(adapter.list_tools())
            finally:
                adapter.close()

        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "echo")
        self.assertEqual(tools[0]["input_schema"]["properties"]["text"]["type"], "string")

    def test_execute_tool_returns_success_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            adapter = _adapter(pathlib.Path(raw_temp_dir))
            try:
                result = adapter.execute_tool("echo", {"text": "hello"}, _context())
            finally:
                adapter.close()

        self.assertTrue(result.ok)
        self.assertEqual(result.output, "echo: hello")

    def test_list_tools_returns_clear_error_when_server_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            adapter = _adapter(pathlib.Path(raw_temp_dir), mode="list_error")
            try:
                with self.assertRaisesRegex(RuntimeError, "tools/list"):
                    list(adapter.list_tools())
            finally:
                adapter.close()

    def test_execute_tool_converts_server_error_to_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            adapter = _adapter(pathlib.Path(raw_temp_dir), mode="call_error")
            try:
                result = adapter.execute_tool("echo", {"text": "hello"}, _context())
            finally:
                adapter.close()

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "mcp_call_failed")
        self.assertIn("call failed", result.output)

    def test_execute_tool_converts_handshake_error_to_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            adapter = _adapter(pathlib.Path(raw_temp_dir), mode="initialize_error")
            try:
                result = adapter.execute_tool("echo", {"text": "hello"}, _context())
            finally:
                adapter.close()

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "mcp_initialize_failed")
        self.assertIn("handshake failed", result.output)

    def test_execute_tool_converts_call_timeout_to_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            adapter = _adapter(pathlib.Path(raw_temp_dir), mode="call_timeout", timeout=0.2)
            try:
                result = adapter.execute_tool("echo", {"text": "hello"}, _context())
            finally:
                adapter.close()

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "mcp_timeout")
        self.assertTrue(result.error.retryable)

    def test_execute_tool_converts_invalid_json_to_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            adapter = _adapter(pathlib.Path(raw_temp_dir), mode="invalid_json_on_call")
            try:
                result = adapter.execute_tool("echo", {"text": "hello"}, _context())
            finally:
                adapter.close()

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "mcp_invalid_json")

    def test_execute_tool_converts_process_exit_to_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp_dir:
            adapter = _adapter(pathlib.Path(raw_temp_dir), mode="exit_on_call")
            try:
                result = adapter.execute_tool("echo", {"text": "hello"}, _context())
            finally:
                adapter.close()

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "mcp_process_exited")


if __name__ == "__main__":
    unittest.main()
