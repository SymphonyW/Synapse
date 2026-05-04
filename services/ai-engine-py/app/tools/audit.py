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
        action: str = "",
        risk_level: str = "",
        details: dict | None = None,
    ) -> None:
        if self._path is None:
            return

        # action 是治理侧最稳定的检索维度。调用方可以显式写入 blocked、
        # approval_required、approved、executed、failed；未传入时兼容旧逻辑，
        # 根据 ok 自动归类为 executed 或 failed。
        normalized_action = action.strip() or ("executed" if ok else "failed")

        # 审计日志只保存输入预览和结构化细节，既能复盘审批/执行链路，
        # 又避免把完整工具输入无限制写入本地日志。
        entry = {
            "timestamp_unix_ms": int(time.time() * 1000),
            "task_id": task_id,
            "user_id": user_id,
            "user_role": user_role,
            "action": normalized_action,
            "tool": tool_name,
            "tool_input_preview": tool_input.strip()[:240],
            "risk_level": risk_level.strip(),
            "ok": ok,
            "outcome": outcome.strip()[:600],
            "reason": reason.strip()[:240],
            "duration_ms": max(0, duration_ms),
            "details": dict(details or {}),
        }

        encoded = json.dumps(entry, ensure_ascii=True, separators=(",", ":"))

        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as output:
                output.write(encoded)
                output.write("\n")
