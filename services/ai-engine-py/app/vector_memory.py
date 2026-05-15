from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Any

from app.embeddings.base import EmbeddingProvider
from app.memory import (
    MemoryRecallHit,
    MemoryRecord,
    MemoryStore,
    _normalize_memory_record,
    _normalize_user_id,
)


ConnectionFactory = Callable[[str], Any]


def _default_connection_factory(database_url: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on runtime packaging
        raise RuntimeError(
            "psycopg is required for vector memory backend; install ai-engine dependencies"
        ) from exc

    return psycopg.connect(database_url, autocommit=True)


class PostgresVectorMemoryStore(MemoryStore):
    """PostgreSQL + pgvector backed semantic memory store."""

    def __init__(
        self,
        database_url: str,
        embedding_provider: EmbeddingProvider,
        embedding_dimension: int,
        top_k: int,
        max_entries_per_user: int,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._database_url = database_url.strip()
        self._embedding_provider = embedding_provider
        self._embedding_dimension = max(1, int(embedding_dimension))
        self._top_k = max(1, int(top_k))
        self._max_entries_per_user = max(1, int(max_entries_per_user))
        self._lock = threading.RLock()

        if not self._database_url:
            raise ValueError("vector database url is required")

        factory = connection_factory or _default_connection_factory
        try:
            self._connection = factory(self._database_url)
            self._ensure_schema()
        except Exception as exc:
            raise RuntimeError(f"failed to initialize vector memory backend: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            close = getattr(self._connection, "close", None)
            if callable(close):
                close()

    def memory_write(self, record: MemoryRecord) -> MemoryRecord | None:
        normalized = _normalize_memory_record(record)
        if normalized is None:
            return None

        try:
            embedding = self._embed_record(normalized)
        except Exception as exc:
            raise RuntimeError(f"failed to embed memory record: {exc}") from exc

        with self._lock:
            self._connection.execute(
                """
                INSERT INTO vector_memories (
                    memory_id,
                    user_id,
                    content,
                    summary,
                    source_task_id,
                    importance,
                    created_at,
                    embedding
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (memory_id)
                DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    content = EXCLUDED.content,
                    summary = EXCLUDED.summary,
                    source_task_id = EXCLUDED.source_task_id,
                    importance = EXCLUDED.importance,
                    created_at = EXCLUDED.created_at,
                    embedding = EXCLUDED.embedding
                """,
                (
                    normalized.memory_id,
                    normalized.user_id,
                    normalized.content,
                    normalized.summary,
                    normalized.source_task_id,
                    normalized.importance,
                    normalized.created_at,
                    _vector_literal(embedding),
                ),
            )
            self._prune_user(normalized.user_id)

        return normalized

    def memory_recall(self, user_id: str, query: str, limit: int) -> list[MemoryRecallHit]:
        normalized_user_id = _normalize_user_id(user_id)
        normalized_query = " ".join(query.strip().split())
        normalized_limit = max(0, limit)
        if not normalized_user_id or not normalized_query or normalized_limit == 0:
            return []

        try:
            query_embedding = self._validate_embedding(
                self._embedding_provider.embed_text(normalized_query)
            )
        except Exception as exc:
            raise RuntimeError(f"failed to embed recall query: {exc}") from exc

        candidate_limit = max(normalized_limit, self._top_k)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT
                    memory_id,
                    user_id,
                    content,
                    summary,
                    source_task_id,
                    importance,
                    created_at,
                    1 - (embedding <=> %s::vector) AS similarity
                FROM vector_memories
                WHERE user_id = %s
                ORDER BY embedding <=> %s::vector ASC, created_at DESC, memory_id ASC
                LIMIT %s
                """,
                (
                    _vector_literal(query_embedding),
                    normalized_user_id,
                    _vector_literal(query_embedding),
                    candidate_limit,
                ),
            ).fetchall()

        hits: list[MemoryRecallHit] = []
        for row in rows:
            record = MemoryRecord(
                memory_id=str(row[0]),
                user_id=str(row[1]),
                content=str(row[2]),
                summary=str(row[3]),
                source_task_id=str(row[4]),
                importance=float(row[5]),
                created_at=int(row[6]),
            )
            similarity = float(row[7])
            score = similarity * 0.85 + record.importance * 0.15
            hits.append(MemoryRecallHit(record=record, score=score, matched_terms=()))

        hits.sort(
            key=lambda hit: (
                hit.score,
                hit.record.importance,
                hit.record.created_at,
                hit.record.memory_id,
            ),
            reverse=True,
        )
        return hits[:normalized_limit]

    def memory_delete(self, user_id: str, memory_id: str) -> bool:
        normalized_user_id = _normalize_user_id(user_id)
        normalized_memory_id = memory_id.strip()
        if not normalized_user_id or not normalized_memory_id:
            return False

        with self._lock:
            cursor = self._connection.execute(
                """
                DELETE FROM vector_memories
                WHERE user_id = %s AND memory_id = %s
                """,
                (normalized_user_id, normalized_memory_id),
            )
        return int(getattr(cursor, "rowcount", 0)) > 0

    def memory_list(self, user_id: str, limit: int) -> list[MemoryRecord]:
        normalized_user_id = _normalize_user_id(user_id)
        normalized_limit = max(0, limit)
        if not normalized_user_id or normalized_limit == 0:
            return []

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT
                    memory_id,
                    user_id,
                    content,
                    summary,
                    source_task_id,
                    importance,
                    created_at
                FROM vector_memories
                WHERE user_id = %s
                ORDER BY created_at DESC, memory_id ASC
                LIMIT %s
                """,
                (normalized_user_id, normalized_limit),
            ).fetchall()

        return [
            MemoryRecord(
                memory_id=str(row[0]),
                user_id=str(row[1]),
                content=str(row[2]),
                summary=str(row[3]),
                source_task_id=str(row[4]),
                importance=float(row[5]),
                created_at=int(row[6]),
            )
            for row in rows
        ]

    def _ensure_schema(self) -> None:
        with self._lock:
            self._connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS vector_memories (
                    memory_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    source_task_id TEXT NOT NULL DEFAULT '',
                    importance DOUBLE PRECISION NOT NULL,
                    created_at BIGINT NOT NULL,
                    embedding VECTOR({self._embedding_dimension}) NOT NULL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_vector_memories_user_id "
                "ON vector_memories (user_id)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_vector_memories_created_at "
                "ON vector_memories (created_at DESC)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_vector_memories_embedding_hnsw "
                "ON vector_memories USING hnsw (embedding vector_cosine_ops)"
            )
            cursor = self._connection.execute(
                """
                SELECT format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                WHERE c.relname = 'vector_memories'
                  AND a.attname = 'embedding'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                """
            )
            row = cursor.fetchone()

        if row is None:
            raise RuntimeError("vector_memories.embedding column was not created")

        actual_dimension = _parse_vector_dimension(str(row[0]))
        if actual_dimension != self._embedding_dimension:
            raise RuntimeError(
                "vector_memories.embedding dimension mismatch: "
                f"expected {self._embedding_dimension}, found {actual_dimension}"
            )

    def _embed_record(self, record: MemoryRecord) -> list[float]:
        embedding_text = "\n".join(
            part for part in (record.content, record.summary) if part.strip()
        )
        return self._validate_embedding(self._embedding_provider.embed_text(embedding_text))

    def _validate_embedding(self, vector: list[float]) -> list[float]:
        normalized = [float(value) for value in vector]
        if len(normalized) != self._embedding_dimension:
            raise ValueError(
                f"embedding dimension mismatch: expected {self._embedding_dimension}, "
                f"got {len(normalized)}"
            )
        return normalized

    def _prune_user(self, user_id: str) -> None:
        self._connection.execute(
            """
            DELETE FROM vector_memories
            WHERE memory_id IN (
                SELECT memory_id
                FROM vector_memories
                WHERE user_id = %s
                ORDER BY created_at DESC, memory_id ASC
                OFFSET %s
            )
            """,
            (user_id, self._max_entries_per_user),
        )


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.12g}" for value in vector) + "]"


def _parse_vector_dimension(type_name: str) -> int:
    match = re.fullmatch(r"vector\((\d+)\)", type_name.strip().lower())
    if match is None:
        raise RuntimeError(f"unexpected vector column type: {type_name}")
    return int(match.group(1))
