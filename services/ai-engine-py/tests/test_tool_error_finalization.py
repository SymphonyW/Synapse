import asyncio
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

from app.runtime import AgentRuntime, OpenAIStreamItem


class _NotFoundAPIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        body = b"<html><head><title>Example Domain</title></head><body>Example Domain</body></html>"
        self.send_response(404)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        _ = (format, args)


class NotFoundAPIServer:
    def __enter__(self) -> "NotFoundAPIServer":
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _NotFoundAPIHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{port}/api"
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class RepeatingSynthesisRuntime(AgentRuntime):
    def __init__(self) -> None:
        super().__init__(
            model_provider="openai",
            openai_api_key="test-key",
            agent_require_approval_for_high_risk=False,
            agent_tool_http_allowlist=("127.0.0.1",),
            agent_tool_http_timeout_seconds=1,
            agent_tool_audit_log_file="",
        )
        self.synthesis_calls = 0

    def _request_openai_stream_with_retry(
        self,
        prompt: str,
        metadata: dict[str, str] | None = None,
    ) -> Iterator[OpenAIStreamItem]:
        _ = (prompt, metadata)
        self.synthesis_calls += 1
        repeated = (
            "HTTP 404 may be caused by a wrong URL, maintenance, server problems, or domain expiry. "
            "For further diagnosis, try checking the URL and contacting the service owner.\n"
        )
        yield OpenAIStreamItem(content=repeated * 4)
        yield OpenAIStreamItem(finish_reason="stop")


async def _collect_text_and_infos(
    runtime: AgentRuntime,
    prompt: str,
) -> tuple[str, list[dict[str, Any]]]:
    import json

    chunks: list[str] = []
    infos: list[dict[str, Any]] = []
    async for event in runtime.run_task(
        task_id="tool-error-finalization",
        user_id="user-1",
        prompt=prompt,
        metadata={
            "agent_enabled": "true",
            "auth_user_role": "admin",
            "memory_write_enabled": "false",
        },
    ):
        if event.kind == "token":
            chunks.append(event.token)
        if event.kind == "info":
            infos.append(json.loads(event.message))
    return "".join(chunks), infos


class ToolErrorFinalizationTests(unittest.TestCase):
    def test_http_404_uses_single_controlled_failure_response_without_synthesis(self) -> None:
        with NotFoundAPIServer() as server:
            runtime = RepeatingSynthesisRuntime()
            answer, infos = asyncio.run(
                _collect_text_and_infos(
                    runtime,
                    f"call {server.url} and summarize response",
                )
            )

        self.assertEqual(runtime.synthesis_calls, 0)
        self.assertIn("HTTP 404", answer)
        self.assertIn("Example Domain", answer)
        self.assertLessEqual(answer.count("For further diagnosis"), 1)
        self.assertEqual(
            [item["agent_event"] for item in infos].count("tool_failed"),
            1,
        )

    def test_same_failed_http_call_is_consumed_once_even_if_prompt_mentions_it_twice(self) -> None:
        with NotFoundAPIServer() as server:
            runtime = RepeatingSynthesisRuntime()
            answer, infos = asyncio.run(
                _collect_text_and_infos(
                    runtime,
                    f"call {server.url}; then summarize {server.url}",
                )
            )

        self.assertEqual(runtime.synthesis_calls, 0)
        self.assertEqual(answer.count("HTTP 404"), 1)
        self.assertEqual(
            [item["agent_event"] for item in infos].count("tool_failed"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
