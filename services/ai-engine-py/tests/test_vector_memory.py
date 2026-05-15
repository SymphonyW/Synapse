import math
import unittest
from typing import Any

from app.embeddings.base import EmbeddingProviderError, MockEmbeddingProvider
from app.memory import MemoryRecord
from app.vector_memory import PostgresVectorMemoryStore


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


class _FakeCursor:
    def __init__(
        self,
        rows: list[tuple[Any, ...]] | None = None,
        rowcount: int = -1,
    ) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class _FakeConnection:
    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.rows: list[dict[str, Any]] = []

    def close(self) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> _FakeCursor:
        normalized = " ".join(query.strip().split()).lower()
        args = params or ()

        if normalized.startswith("create extension"):
            return _FakeCursor()
        if normalized.startswith("create table"):
            return _FakeCursor()
        if normalized.startswith("create index"):
            return _FakeCursor()
        if normalized.startswith("select format_type"):
            return _FakeCursor(rows=[(f"vector({self.dimension})",)])

        if normalized.startswith("insert into vector_memories"):
            (
                memory_id,
                user_id,
                content,
                summary,
                source_task_id,
                importance,
                created_at,
                embedding_literal,
            ) = args
            embedding = [
                float(item)
                for item in str(embedding_literal).strip("[]").split(",")
                if item.strip()
            ]
            self.rows = [item for item in self.rows if item["memory_id"] != memory_id]
            self.rows.append(
                {
                    "memory_id": memory_id,
                    "user_id": user_id,
                    "content": content,
                    "summary": summary,
                    "source_task_id": source_task_id,
                    "importance": importance,
                    "created_at": created_at,
                    "embedding": embedding,
                }
            )
            return _FakeCursor(rowcount=1)

        if normalized.startswith("delete from vector_memories where user_id = %s and memory_id = %s"):
            user_id, memory_id = args
            before = len(self.rows)
            self.rows = [
                item
                for item in self.rows
                if not (item["user_id"] == user_id and item["memory_id"] == memory_id)
            ]
            return _FakeCursor(rowcount=before - len(self.rows))

        if normalized.startswith("delete from vector_memories where memory_id in"):
            user_id, keep_limit = args
            ordered = sorted(
                [item for item in self.rows if item["user_id"] == user_id],
                key=lambda item: (item["created_at"], item["memory_id"]),
                reverse=True,
            )
            keep_ids = {item["memory_id"] for item in ordered[:keep_limit]}
            before = len(self.rows)
            self.rows = [
                item
                for item in self.rows
                if item["user_id"] != user_id or item["memory_id"] in keep_ids
            ]
            return _FakeCursor(rowcount=before - len(self.rows))

        if "from vector_memories" in normalized and "embedding <=>" in normalized:
            embedding_literal, user_id, _ordering_embedding_literal, candidate_limit = args
            query_vector = [
                float(item)
                for item in str(embedding_literal).strip("[]").split(",")
                if item.strip()
            ]
            candidates = []
            for item in self.rows:
                if item["user_id"] != user_id:
                    continue
                similarity = _cosine_similarity(query_vector, item["embedding"])
                candidates.append(
                    (
                        item["memory_id"],
                        item["user_id"],
                        item["content"],
                        item["summary"],
                        item["source_task_id"],
                        item["importance"],
                        item["created_at"],
                        similarity,
                    )
                )
            candidates.sort(key=lambda row: (row[7], row[6], row[0]), reverse=True)
            return _FakeCursor(rows=candidates[:candidate_limit])

        if normalized.startswith(
            "select memory_id, user_id, content, summary, source_task_id, importance, created_at"
        ):
            user_id, limit = args
            rows = [
                (
                    item["memory_id"],
                    item["user_id"],
                    item["content"],
                    item["summary"],
                    item["source_task_id"],
                    item["importance"],
                    item["created_at"],
                )
                for item in sorted(
                    [row for row in self.rows if row["user_id"] == user_id],
                    key=lambda item: (item["created_at"], item["memory_id"]),
                    reverse=True,
                )[:limit]
            ]
            return _FakeCursor(rows=rows)

        raise AssertionError(f"unexpected query: {query}")


class _FailingEmbeddingProvider:
    def embed_text(self, text: str) -> list[float]:
        raise EmbeddingProviderError(f"cannot embed: {text}")


class VectorMemoryStoreTests(unittest.TestCase):
    def _make_store(self) -> PostgresVectorMemoryStore:
        self.connection = _FakeConnection(dimension=3)
        self.provider = MockEmbeddingProvider(
            {
                "Gateway retries should be bounded.\nbounded gateway retries": [1.0, 0.0, 0.0],
                "Vector recall keeps semantic neighbors close.\nsemantic neighbors": [0.8, 0.2, 0.0],
                "Unrelated astronomy note.\nspace": [0.0, 1.0, 0.0],
                "gateway reliability": [1.0, 0.0, 0.0],
            }
        )
        return PostgresVectorMemoryStore(
            database_url="postgresql://fake",
            embedding_provider=self.provider,
            embedding_dimension=3,
            top_k=8,
            max_entries_per_user=10,
            connection_factory=lambda _: self.connection,
        )

    def test_write_recall_list_delete_and_user_isolation(self) -> None:
        store = self._make_store()

        first = store.memory_write(
            MemoryRecord(
                memory_id="mem-1",
                user_id="Alice",
                content="Gateway retries should be bounded.",
                summary="bounded gateway retries",
                source_task_id="task-1",
                importance=0.9,
                created_at=100,
            )
        )
        second = store.memory_write(
            MemoryRecord(
                memory_id="mem-2",
                user_id="alice",
                content="Vector recall keeps semantic neighbors close.",
                summary="semantic neighbors",
                source_task_id="task-2",
                importance=0.7,
                created_at=200,
            )
        )
        store.memory_write(
            MemoryRecord(
                memory_id="mem-3",
                user_id="bob",
                content="Unrelated astronomy note.",
                summary="space",
                source_task_id="task-3",
                importance=0.8,
                created_at=300,
            )
        )

        self.assertIsNotNone(first)
        self.assertEqual(first.user_id, "alice")
        self.assertEqual([item.memory_id for item in store.memory_list("alice", 10)], ["mem-2", "mem-1"])

        hits = store.memory_recall("alice", "gateway reliability", limit=2)
        self.assertEqual([hit.record.memory_id for hit in hits], ["mem-1", "mem-2"])
        self.assertEqual(hits[0].matched_terms, ())
        self.assertGreater(hits[0].score, hits[1].score)

        self.assertTrue(store.memory_delete("alice", second.memory_id))
        self.assertEqual([item.memory_id for item in store.memory_list("alice", 10)], ["mem-1"])
        self.assertFalse(store.memory_delete("alice", "mem-3"))
        self.assertEqual([item.memory_id for item in store.memory_list("bob", 10)], ["mem-3"])

    def test_empty_query_and_limit_behavior(self) -> None:
        store = self._make_store()
        store.memory_write(
            MemoryRecord(
                memory_id="mem-1",
                user_id="alice",
                content="Gateway retries should be bounded.",
                summary="bounded gateway retries",
                source_task_id="task-1",
                importance=0.9,
                created_at=100,
            )
        )
        store.memory_write(
            MemoryRecord(
                memory_id="mem-2",
                user_id="alice",
                content="Vector recall keeps semantic neighbors close.",
                summary="semantic neighbors",
                source_task_id="task-2",
                importance=0.7,
                created_at=200,
            )
        )

        self.assertEqual(store.memory_recall("alice", "   ", limit=3), [])
        self.assertEqual(store.memory_recall("alice", "gateway reliability", limit=0), [])
        self.assertEqual(len(store.memory_recall("alice", "gateway reliability", limit=1)), 1)

    def test_score_sorting_uses_created_at_as_stable_tie_breaker(self) -> None:
        store = self._make_store()
        self.provider.vectors_by_text.update(
            {
                "Older.\nshared": [1.0, 0.0, 0.0],
                "Newer.\nshared": [1.0, 0.0, 0.0],
            }
        )
        store.memory_write(
            MemoryRecord(
                memory_id="mem-old",
                user_id="alice",
                content="Older.",
                summary="shared",
                source_task_id="task-old",
                importance=0.5,
                created_at=100,
            )
        )
        store.memory_write(
            MemoryRecord(
                memory_id="mem-new",
                user_id="alice",
                content="Newer.",
                summary="shared",
                source_task_id="task-new",
                importance=0.5,
                created_at=200,
            )
        )

        hits = store.memory_recall("alice", "gateway reliability", limit=2)

        self.assertEqual([hit.record.memory_id for hit in hits], ["mem-new", "mem-old"])

    def test_embedding_failure_is_not_silently_swallowed(self) -> None:
        connection = _FakeConnection(dimension=3)
        store = PostgresVectorMemoryStore(
            database_url="postgresql://fake",
            embedding_provider=_FailingEmbeddingProvider(),
            embedding_dimension=3,
            top_k=8,
            max_entries_per_user=10,
            connection_factory=lambda _: connection,
        )

        with self.assertRaisesRegex(RuntimeError, "failed to embed memory record"):
            store.memory_write(
                MemoryRecord(
                    memory_id="mem-1",
                    user_id="alice",
                    content="Gateway retries should be bounded.",
                    summary="bounded gateway retries",
                    source_task_id="task-1",
                    importance=0.9,
                    created_at=100,
                )
            )

    def test_backend_initialization_failure_is_explicit(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "failed to initialize vector memory backend"):
            PostgresVectorMemoryStore(
                database_url="postgresql://fake",
                embedding_provider=MockEmbeddingProvider({}),
                embedding_dimension=3,
                top_k=8,
                max_entries_per_user=10,
                connection_factory=lambda _: (_ for _ in ()).throw(RuntimeError("db down")),
            )


if __name__ == "__main__":
    unittest.main()
