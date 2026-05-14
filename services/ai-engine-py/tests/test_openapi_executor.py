import asyncio
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.runtime import AgentRuntime
from app.tools import ToolCall, ToolContext
from app.tools.openapi_executor import OpenAPIHTTPExecutor
from app.tools.providers import OpenAPIToolProvider


class _MockAPIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/items":
            payload = {
                "query": parse_qs(parsed.query),
                "trace": self.headers.get("X-Trace-Id", ""),
                "static": self.headers.get("X-Static", ""),
                "authorization": self.headers.get("Authorization", ""),
                "api_key": self.headers.get("X-API-Key", ""),
            }
            self._json(200, payload)
            return
        if parsed.path.startswith("/items/") and parsed.path.endswith("/detail"):
            item_id = parsed.path.removeprefix("/items/").removesuffix("/detail")
            self._json(200, {"item_id": item_id})
            return
        if parsed.path == "/text":
            self._text(200, "plain response")
            return
        if parsed.path == "/slow":
            time.sleep(1.2)
            self._json(200, {"ok": True})
            return
        if parsed.path == "/status/500":
            self._json(500, {"error": "upstream failed"})
            return
        if parsed.path == "/large":
            self._text(200, "x" * 256)
            return
        if parsed.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://example.com/outside")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""
        body = json.loads(raw_body) if raw_body else None
        self._json(
            201,
            {
                "body": body,
                "content_type": self.headers.get("Content-Type", ""),
                "authorization": self.headers.get("Authorization", ""),
                "api_key": self.headers.get("X-API-Key", ""),
                "static": self.headers.get("X-Static", ""),
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        _ = (format, args)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _text(self, status: int, payload: str) -> None:
        encoded = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class MockAPIServer:
    def __enter__(self) -> "MockAPIServer":
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockAPIHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _context() -> ToolContext:
    return ToolContext(
        task_id="task-1",
        user_id="user-1",
        user_role="admin",
        prompt="openapi executor smoke test",
        metadata={},
        recalled_memories=[],
    )


def _executor(**overrides: Any) -> OpenAPIHTTPExecutor:
    config = {
        "allowlist": ("127.0.0.1",),
        "timeout_seconds": 0.5,
        "max_response_bytes": 512,
        "allowed_schemes": ("http", "https"),
    }
    config.update(overrides)
    return OpenAPIHTTPExecutor(**config)


async def _collect_runtime_infos(
    runtime: AgentRuntime,
    metadata: dict[str, str],
) -> list[dict[str, Any]]:
    infos: list[dict[str, Any]] = []
    async for event in runtime.run_task(
        task_id="openapi-runtime",
        user_id="user-1",
        prompt="call the openapi tool",
        metadata=metadata,
    ):
        if event.kind == "info":
            infos.append(json.loads(event.message))
    return infos


class OpenAPIHTTPExecutorTests(unittest.TestCase):
    def test_get_query_params_returns_json_response(self) -> None:
        with MockAPIServer() as server:
            result = _executor()(
                {
                    "method": "GET",
                    "path": "/items",
                    "base_url": server.base_url,
                    "parameters": [
                        {"name": "q", "in": "query", "required": True},
                        {"name": "page", "in": "query"},
                    ],
                },
                ToolCall(tool_name="openapi_get_items", arguments={"q": "alpha", "page": 2}),
                _context(),
            )

        self.assertTrue(result.ok)
        self.assertIn('"q":["alpha"]', result.output)
        self.assertIn('"page":["2"]', result.output)
        self.assertEqual(result.metadata["status_code"], 200)

    def test_post_json_body_and_auth_headers(self) -> None:
        with MockAPIServer() as server:
            result = _executor(
                static_headers={"X-Static": "static-value"},
                bearer_token="secret-token",
                api_key_header="X-API-Key",
                api_key_value="secret-key",
            )(
                {
                    "method": "POST",
                    "path": "/items",
                    "base_url": server.base_url,
                    "request_body_required": True,
                },
                ToolCall(
                    tool_name="openapi_create_item",
                    arguments={"body": {"name": "created"}},
                ),
                _context(),
            )

        self.assertTrue(result.ok)
        self.assertIn('"body":{"name":"created"}', result.output)
        self.assertIn('"authorization":"Bearer [redacted]"', result.output)
        self.assertIn('"api_key":"[redacted]"', result.output)
        self.assertIn('"static":"[redacted]"', result.output)

    def test_replaces_path_params(self) -> None:
        with MockAPIServer() as server:
            result = _executor()(
                {
                    "method": "GET",
                    "path": "/items/{item_id}/detail",
                    "base_url": server.base_url,
                    "parameters": [{"name": "item_id", "in": "path", "required": True}],
                },
                ToolCall(tool_name="openapi_get_item", arguments={"item_id": "abc 123"}),
                _context(),
            )

        self.assertTrue(result.ok)
        self.assertIn('"item_id":"abc%20123"', result.output)

    def test_passes_header_params(self) -> None:
        with MockAPIServer() as server:
            result = _executor()(
                {
                    "method": "GET",
                    "path": "/items",
                    "base_url": server.base_url,
                    "parameters": [{"name": "X-Trace-Id", "in": "header"}],
                },
                ToolCall(tool_name="openapi_get_items", arguments={"X-Trace-Id": "trace-1"}),
                _context(),
            )

        self.assertTrue(result.ok)
        self.assertIn('"trace":"trace-1"', result.output)

    def test_rejects_non_allowlisted_host(self) -> None:
        result = _executor(allowlist=("api.example.com",))(
            {"method": "GET", "path": "/items", "base_url": "http://127.0.0.1:1"},
            ToolCall(tool_name="openapi_get_items", arguments={}),
            _context(),
        )

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "openapi_host_not_allowed")

    def test_timeout_returns_structured_failure(self) -> None:
        with MockAPIServer() as server:
            result = _executor(timeout_seconds=0.1)(
                {"method": "GET", "path": "/slow", "base_url": server.base_url},
                ToolCall(tool_name="openapi_slow", arguments={}),
                _context(),
            )

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "openapi_timeout")
        self.assertTrue(result.error.retryable)

    def test_http_error_returns_status_and_body(self) -> None:
        with MockAPIServer() as server:
            result = _executor()(
                {"method": "GET", "path": "/status/500", "base_url": server.base_url},
                ToolCall(tool_name="openapi_fail", arguments={}),
                _context(),
            )

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "openapi_http_error")
        self.assertEqual(result.error.details["status_code"], 500)
        self.assertIn("upstream failed", result.output)

    def test_text_response_is_returned(self) -> None:
        with MockAPIServer() as server:
            result = _executor()(
                {"method": "GET", "path": "/text", "base_url": server.base_url},
                ToolCall(tool_name="openapi_text", arguments={}),
                _context(),
            )

        self.assertTrue(result.ok)
        self.assertIn("plain response", result.output)
        self.assertEqual(result.metadata["content_type"], "text/plain")

    def test_large_response_returns_truncated_failure(self) -> None:
        with MockAPIServer() as server:
            result = _executor(max_response_bytes=32)(
                {"method": "GET", "path": "/large", "base_url": server.base_url},
                ToolCall(tool_name="openapi_large", arguments={}),
                _context(),
            )

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "openapi_response_too_large")
        self.assertLessEqual(len(result.output), 160)
        self.assertTrue(result.error.details["truncated"])

    def test_cross_domain_redirect_is_blocked(self) -> None:
        with MockAPIServer() as server:
            result = _executor()(
                {"method": "GET", "path": "/redirect", "base_url": server.base_url},
                ToolCall(tool_name="openapi_redirect", arguments={}),
                _context(),
            )

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, "openapi_redirect_blocked")

    def test_runtime_executes_registered_openapi_get_tool(self) -> None:
        with MockAPIServer() as server:
            spec = {
                "servers": [{"url": server.base_url}],
                "paths": {
                    "/items": {
                        "get": {
                            "operationId": "getItems",
                            "parameters": [
                                {"name": "q", "in": "query", "required": True},
                                {"name": "page", "in": "query"},
                            ],
                        }
                    }
                },
            }
            runtime = AgentRuntime(
                model_provider="mock",
                agent_tool_audit_log_file="",
                agent_tool_providers=(
                    OpenAPIToolProvider(spec, executor=_executor()),
                ),
            )
            infos = asyncio.run(
                _collect_runtime_infos(
                    runtime,
                    {
                        "agent_enabled": "true",
                        "auth_user_role": "admin",
                        "agent_required_tool": "openapi_get_items",
                        "agent_required_tool_input": '{"q":"alpha","page":2}',
                    },
                )
            )

        phases = [item["agent_event"] for item in infos]
        self.assertIn("tool_finished", phases)
        finished = next(item for item in infos if item["agent_event"] == "tool_finished")
        self.assertEqual(finished["payload"]["tool"], "openapi_get_items")
        self.assertIn('"q":["alpha"]', finished["payload"]["output"])
        self.assertIn('"page":["2"]', finished["payload"]["output"])


if __name__ == "__main__":
    unittest.main()
