import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.memory import FileMemoryStore, MemoryRecord
from app.runtime import AgentRuntime


async def _collect_runtime_infos(
    runtime: AgentRuntime,
    prompt: str,
) -> list[dict[str, Any]]:
    infos: list[dict[str, Any]] = []
    async for event in runtime.run_task(
        task_id="memory-task",
        user_id="memory-user",
        prompt=prompt,
        metadata={"memory_write_enabled": "false"},
    ):
        if event.kind == "info":
            infos.append(json.loads(event.message))
    return infos


class MemoryStoreTests(unittest.TestCase):
    def test_file_memory_store_write_recall_list_delete(self) -> None:
        # file backend 仍使用关键词召回，但对外只暴露 MemoryStore 协议方法。
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_file = Path(tmp_dir) / "memory.json"
            store = FileMemoryStore(str(memory_file), max_entries_per_user=8)

            written = store.memory_write(
                MemoryRecord(
                    memory_id="",
                    user_id="Memory-User",
                    content="Gateway retries should be bounded and audited.",
                    summary="bounded gateway retries",
                    source_task_id="task-1",
                    importance=0.9,
                    created_at=100,
                )
            )

            self.assertIsNotNone(written)
            self.assertEqual(written.user_id, "memory-user")
            self.assertTrue(written.memory_id)
            self.assertEqual(written.source_task_id, "task-1")
            self.assertEqual(written.created_at, 100)

            listed = store.memory_list("memory-user", limit=10)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].summary, "bounded gateway retries")

            hits = store.memory_recall("memory-user", "gateway audit retries", limit=3)
            self.assertEqual(len(hits), 1)
            self.assertGreater(hits[0].score, 0.0)
            self.assertIn("gateway", hits[0].matched_terms)

            self.assertTrue(store.memory_delete("memory-user", written.memory_id))
            self.assertEqual(store.memory_list("memory-user", limit=10), [])
            self.assertFalse(store.memory_delete("memory-user", written.memory_id))

    def test_runtime_emits_explicit_memory_recall_event(self) -> None:
        # Agent 执行前的召回命中会作为标准 info 事件输出，Gateway 可以原样持久化并展示。
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_file = Path(tmp_dir) / "memory.json"
            runtime = AgentRuntime(
                model_provider="mock",
                agent_memory_file=str(memory_file),
                agent_tool_audit_log_file="",
            )
            written = runtime.memory_write(
                user_id="memory-user",
                content="Gateway retries are bounded and observable.",
                summary="bounded gateway retries",
                source_task_id="seed-task",
                importance=0.9,
            )
            self.assertIsNotNone(written)

            infos = asyncio.run(_collect_runtime_infos(runtime, "recall gateway retries"))
            recall_event = next(item for item in infos if item["agent_event"] == "memory_recall")

            self.assertEqual(recall_event["schema"], "synapse.agent.info.v1")
            self.assertEqual(recall_event["payload"]["hit_count"], 1)
            self.assertEqual(
                recall_event["payload"]["hits"][0]["source_task_id"],
                "seed-task",
            )
            self.assertIn("display_message", recall_event)


if __name__ == "__main__":
    unittest.main()
