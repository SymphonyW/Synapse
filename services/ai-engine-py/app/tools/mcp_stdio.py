import itertools
import json
import os
import queue
import subprocess
import threading
import time
from typing import Any, Iterable

from app.tools.base import ToolContext, ToolResult


_STDOUT_CLOSED = object()
_DEFAULT_INPUT_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": True}


class StdioMCPError(RuntimeError):
    def __init__(
        self,
        message: str,
        code: str = "mcp_error",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = dict(details or {})


class StdioMCPAdapter:
    """Minimal stdio MCP adapter used behind MCPToolProvider.

    The adapter owns JSON-RPC framing, subprocess lifecycle and protocol errors.
    Runtime only sees standard ToolResult objects through the existing provider
    abstraction, so policy, approval and audit behavior stays centralized.
    """

    def __init__(
        self,
        command: str,
        args: Iterable[str] = (),
        env: dict[str, str] | None = None,
        working_dir: str = "",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.command = command.strip()
        self.args = tuple(str(arg) for arg in args)
        self.env = {str(key): str(value) for key, value in (env or {}).items()}
        self.working_dir = working_dir.strip()
        self.timeout_seconds = max(0.1, float(timeout_seconds))

        self._process: subprocess.Popen[str] | None = None
        self._stdout_queue: queue.Queue[str | object] = queue.Queue()
        self._stderr_tail: list[str] = []
        self._id_counter = itertools.count(1)
        self._lock = threading.RLock()
        self._initialized = False

    def list_tools(self) -> Iterable[dict[str, Any]]:
        with self._lock:
            self._ensure_started_locked()
            try:
                result = self._request_locked("tools/list", {}, error_code="mcp_list_failed")
            except StdioMCPError as exc:
                self._maybe_reset_locked(exc)
                raise

        return tuple(self._normalize_tool_descriptor(item) for item in self._extract_tools(result))

    def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        _ = context
        try:
            with self._lock:
                self._ensure_started_locked()
                result = self._request_locked(
                    "tools/call",
                    {"name": tool_name, "arguments": dict(arguments)},
                    error_code="mcp_call_failed",
                )
        except StdioMCPError as exc:
            with self._lock:
                self._maybe_reset_locked(exc)
            return ToolResult.failure(
                str(exc),
                code=exc.code,
                retryable=exc.retryable,
                details=exc.details,
            )

        if isinstance(result, dict) and result.get("isError") is True:
            output = self._extract_output(result) or f"mcp tool {tool_name} returned an error"
            return ToolResult.failure(
                output,
                code="mcp_tool_error",
                details={"tool_name": tool_name, "mcp_result": result},
            )

        return ToolResult.success(
            self._extract_output(result),
            metadata={"mcp_result": result if isinstance(result, dict) else {"result": result}},
        )

    def close(self) -> None:
        with self._lock:
            self._stop_process_locked()

    def _ensure_started_locked(self) -> None:
        if not self.command:
            raise StdioMCPError(
                "mcp stdio command is required",
                code="mcp_start_failed",
            )

        if self._process is not None and self._process.poll() is None and self._initialized:
            return
        if self._process is not None and self._process.poll() is not None:
            self._stop_process_locked()

        merged_env = os.environ.copy()
        merged_env.update(self.env)
        try:
            self._process = subprocess.Popen(
                (self.command, *self.args),
                cwd=self.working_dir or None,
                env=merged_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise StdioMCPError(
                f"failed to start mcp stdio server: {exc}",
                code="mcp_start_failed",
                details={"command": self.command, "args": list(self.args)},
            ) from exc

        self._stdout_queue = queue.Queue()
        self._stderr_tail = []
        self._initialized = False
        self._start_reader_threads_locked()

        try:
            self._request_locked(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "synapse-ai-engine", "version": "0.1"},
                },
                error_code="mcp_initialize_failed",
            )
            self._send_notification_locked("notifications/initialized", {})
            self._initialized = True
        except StdioMCPError:
            self._stop_process_locked()
            raise

    def _start_reader_threads_locked(self) -> None:
        process = self._require_process_locked()
        if process.stdout is not None:
            stdout_thread = threading.Thread(
                target=self._read_stdout,
                args=(process,),
                name="synapse-mcp-stdout",
                daemon=True,
            )
            stdout_thread.start()
        if process.stderr is not None:
            stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(process,),
                name="synapse-mcp-stderr",
                daemon=True,
            )
            stderr_thread.start()

    def _read_stdout(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self._stdout_queue.put(line)
        self._stdout_queue.put(_STDOUT_CLOSED)

    def _read_stderr(self, process: subprocess.Popen[str]) -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stripped = line.strip()
            if stripped:
                self._stderr_tail.append(stripped[:500])
                del self._stderr_tail[:-8]

    def _request_locked(
        self,
        method: str,
        params: dict[str, Any],
        error_code: str,
    ) -> Any:
        message_id = next(self._id_counter)
        self._write_message_locked(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "method": method,
                "params": params,
            }
        )
        response = self._read_response_locked(message_id, method)
        if "error" in response:
            raise self._json_rpc_error(method, response["error"], error_code)
        if "result" not in response:
            raise StdioMCPError(
                f"mcp {method} response is missing result",
                code=error_code,
                details={"method": method},
            )
        return response["result"]

    def _send_notification_locked(self, method: str, params: dict[str, Any]) -> None:
        self._write_message_locked({"jsonrpc": "2.0", "method": method, "params": params})

    def _write_message_locked(self, payload: dict[str, Any]) -> None:
        process = self._require_process_locked()
        if process.poll() is not None:
            raise StdioMCPError(
                f"mcp stdio server exited with code {process.returncode}",
                code="mcp_process_exited",
                details=self._process_details_locked(),
            )
        if process.stdin is None:
            raise StdioMCPError("mcp stdio stdin is not available", code="mcp_start_failed")

        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise StdioMCPError(
                f"failed to write to mcp stdio server: {exc}",
                code="mcp_process_exited",
                details=self._process_details_locked(),
            ) from exc

    def _read_response_locked(
        self,
        message_id: int,
        method: str,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StdioMCPError(
                    f"mcp {method} timed out after {self.timeout_seconds:g}s",
                    code="mcp_timeout",
                    retryable=True,
                    details={"method": method, **self._process_details_locked()},
                )
            try:
                raw = self._stdout_queue.get(timeout=remaining)
            except queue.Empty as exc:
                raise StdioMCPError(
                    f"mcp {method} timed out after {self.timeout_seconds:g}s",
                    code="mcp_timeout",
                    retryable=True,
                    details={"method": method, **self._process_details_locked()},
                ) from exc

            if raw is _STDOUT_CLOSED:
                raise StdioMCPError(
                    f"mcp stdio server exited before {method} completed",
                    code="mcp_process_exited",
                    details={"method": method, **self._process_details_locked()},
                )

            raw_text = str(raw).strip()
            if not raw_text:
                continue

            try:
                decoded = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise StdioMCPError(
                    f"mcp {method} returned invalid JSON: {exc.msg}",
                    code="mcp_invalid_json",
                    details={"method": method, "raw": raw_text[:500]},
                ) from exc

            if not isinstance(decoded, dict):
                raise StdioMCPError(
                    f"mcp {method} returned non-object JSON-RPC response",
                    code="mcp_invalid_json",
                    details={"method": method, "raw": raw_text[:500]},
                )

            if decoded.get("id") != message_id:
                continue
            return decoded

    def _json_rpc_error(self, method: str, error: Any, code: str) -> StdioMCPError:
        if isinstance(error, dict):
            message = str(error.get("message", "")).strip() or "unknown error"
            details = {"method": method, "mcp_error": dict(error)}
        else:
            message = str(error).strip() or "unknown error"
            details = {"method": method, "mcp_error": error}
        return StdioMCPError(f"mcp {method} failed: {message}", code=code, details=details)

    def _extract_tools(self, result: Any) -> tuple[dict[str, Any], ...]:
        if isinstance(result, dict):
            raw_tools = result.get("tools", [])
        else:
            raw_tools = result
        if not isinstance(raw_tools, list):
            raise StdioMCPError(
                "mcp tools/list returned an invalid tools payload",
                code="mcp_list_failed",
            )
        return tuple(item for item in raw_tools if isinstance(item, dict))

    def _normalize_tool_descriptor(self, descriptor: dict[str, Any]) -> dict[str, Any]:
        name = str(descriptor.get("name", "")).strip()
        schema = (
            descriptor.get("input_schema")
            or descriptor.get("inputSchema")
            or descriptor.get("schema")
            or _DEFAULT_INPUT_SCHEMA
        )
        if not isinstance(schema, dict) or not schema:
            schema = _DEFAULT_INPUT_SCHEMA

        normalized = {
            "name": name,
            "description": str(descriptor.get("description", "")).strip()
            or f"MCP tool {name}",
            "input_schema": dict(schema),
        }
        if "risk_level" in descriptor:
            normalized["risk_level"] = descriptor["risk_level"]
        if "requires_approval" in descriptor:
            normalized["requires_approval"] = descriptor["requires_approval"]
        return normalized

    def _extract_output(self, result: Any) -> str:
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                fragments: list[str] = []
                for item in content:
                    if isinstance(item, str):
                        fragments.append(item)
                    elif isinstance(item, dict) and isinstance(item.get("text"), str):
                        fragments.append(item["text"])
                    elif isinstance(item, dict):
                        fragments.append(json.dumps(item, ensure_ascii=True, separators=(",", ":")))
                return "\n".join(fragment for fragment in fragments if fragment)

            if "structuredContent" in result:
                return json.dumps(result["structuredContent"], ensure_ascii=True, separators=(",", ":"))
            if "result" in result:
                return json.dumps(result["result"], ensure_ascii=True, separators=(",", ":"))

        return json.dumps(result, ensure_ascii=True, separators=(",", ":"))

    def _require_process_locked(self) -> subprocess.Popen[str]:
        if self._process is None:
            raise StdioMCPError("mcp stdio server is not started", code="mcp_start_failed")
        return self._process

    def _maybe_reset_locked(self, exc: StdioMCPError) -> None:
        if exc.code in {"mcp_timeout", "mcp_invalid_json", "mcp_process_exited", "mcp_start_failed"}:
            self._stop_process_locked()

    def _stop_process_locked(self) -> None:
        process = self._process
        self._process = None
        self._initialized = False
        if process is None:
            return

        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass

    def _process_details_locked(self) -> dict[str, Any]:
        process = self._process
        return {
            "command": self.command,
            "args": list(self.args),
            "returncode": process.returncode if process is not None else None,
            "stderr_tail": list(self._stderr_tail),
        }
