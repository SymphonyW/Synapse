import json
import pathlib
import threading
import time


class ToolAuditLogger:
    def __init__(self, file_path: str) -> None:
        self._path = pathlib.Path(file_path).expanduser() if file_path.strip() else None
        self._lock = threading.Lock()

    def log(
        self,
        task_id: str,
        user_id: str,
        user_role: str,
        tool_name: str,
        tool_input: str,
        outcome: str,
        ok: bool,
        duration_ms: int,
        reason: str = "",
    ) -> None:
        if self._path is None:
            return

        entry = {
            "timestamp_unix_ms": int(time.time() * 1000),
            "task_id": task_id,
            "user_id": user_id,
            "user_role": user_role,
            "tool": tool_name,
            "tool_input_preview": tool_input.strip()[:240],
            "ok": ok,
            "outcome": outcome.strip()[:600],
            "reason": reason.strip()[:240],
            "duration_ms": max(0, duration_ms),
        }

        encoded = json.dumps(entry, ensure_ascii=True, separators=(",", ":"))

        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as output:
                output.write(encoded)
                output.write("\n")
