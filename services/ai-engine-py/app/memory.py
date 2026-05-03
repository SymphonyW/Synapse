import json
import pathlib
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class MemoryRecord:
    """长期记忆的稳定记录结构。

    `memory_id` 是删除和 API 管理需要的内部主键；用户要求的字段保持一等字段，
    便于未来 file/vector backend 共用同一份外部契约。
    """

    memory_id: str
    user_id: str
    content: str
    summary: str
    source_task_id: str
    importance: float
    created_at: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "user_id": self.user_id,
            "content": self.content,
            "summary": self.summary,
            "source_task_id": self.source_task_id,
            "importance": self.importance,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class MemoryRecallHit:
    record: MemoryRecord
    score: float
    matched_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = self.record.to_dict()
        payload["score"] = round(self.score, 4)
        payload["matched_terms"] = list(self.matched_terms)
        return payload


class MemoryStore(Protocol):
    """长期记忆后端接口。

    file backend 使用关键词打分；未来 vector backend 可以在相同方法内替换召回算法，
    runtime、工具和 API 不需要关心底层索引形态。
    """

    def memory_write(self, record: MemoryRecord) -> MemoryRecord | None:
        ...

    def memory_recall(self, user_id: str, query: str, limit: int) -> list[MemoryRecallHit]:
        ...

    def memory_delete(self, user_id: str, memory_id: str) -> bool:
        ...

    def memory_list(self, user_id: str, limit: int) -> list[MemoryRecord]:
        ...


class FileMemoryStore:
    """文件型长期记忆后端。

    当前实现仍是轻量关键词召回，但文件内写入新 schema，并在读取时兼容旧版
    `prompt/summary/final_response_preview` 记录，避免升级后丢失已有记忆。
    """

    def __init__(self, file_path: str, max_entries_per_user: int) -> None:
        self._path = pathlib.Path(file_path).expanduser() if file_path.strip() else None
        self._max_entries_per_user = max(1, max_entries_per_user)
        self._lock = threading.Lock()

    def memory_write(self, record: MemoryRecord) -> MemoryRecord | None:
        if self._path is None:
            return None

        normalized = self._normalize_record(record)
        if normalized is None:
            return None

        with self._lock:
            document = self._load_document()
            users = self._ensure_users(document)
            records = users.setdefault(normalized.user_id, [])
            if not isinstance(records, list):
                records = []

            records.append(normalized.to_dict())
            users[normalized.user_id] = records[-self._max_entries_per_user :]
            self._save_document(document)

        return normalized

    def memory_recall(self, user_id: str, query: str, limit: int) -> list[MemoryRecallHit]:
        normalized_user_id = _normalize_user_id(user_id)
        normalized_limit = max(0, limit)
        if self._path is None or not normalized_user_id or normalized_limit == 0:
            return []

        with self._lock:
            document = self._load_document()
            records = self._records_for_user(document, normalized_user_id)
        if not records:
            return []

        query_tokens = _tokenize(query)
        scored: list[tuple[float, int, MemoryRecallHit]] = []
        for record in records:
            haystack = f"{record.content} {record.summary}"
            record_tokens = _tokenize(haystack)
            matched_terms = tuple(sorted(record_tokens.intersection(query_tokens)))
            overlap = len(matched_terms)
            score = overlap + max(0.0, min(1.0, record.importance)) * 0.2
            scored.append(
                (
                    score,
                    record.created_at,
                    MemoryRecallHit(
                        record=record,
                        score=score,
                        matched_terms=matched_terms,
                    ),
                )
            )

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        hits = [hit for _, _, hit in scored if hit.matched_terms]
        if hits:
            return hits[:normalized_limit]

        # 没有关键词命中时回退到最近记忆，保持旧实现“有兜底上下文”的行为。
        latest = sorted(records, key=lambda item: item.created_at, reverse=True)
        return [
            MemoryRecallHit(record=record, score=0.0, matched_terms=())
            for record in latest[:normalized_limit]
        ]

    def memory_delete(self, user_id: str, memory_id: str) -> bool:
        normalized_user_id = _normalize_user_id(user_id)
        normalized_memory_id = memory_id.strip()
        if self._path is None or not normalized_user_id or not normalized_memory_id:
            return False

        with self._lock:
            document = self._load_document()
            users = self._ensure_users(document)
            records = users.get(normalized_user_id, [])
            if not isinstance(records, list):
                return False

            kept: list[dict[str, Any]] = []
            deleted = False
            for item in records:
                if not isinstance(item, dict):
                    continue
                if str(item.get("memory_id", "")).strip() == normalized_memory_id:
                    deleted = True
                    continue
                kept.append(item)

            if not deleted:
                return False

            users[normalized_user_id] = kept
            self._save_document(document)
            return True

    def memory_list(self, user_id: str, limit: int) -> list[MemoryRecord]:
        normalized_user_id = _normalize_user_id(user_id)
        normalized_limit = max(0, limit)
        if self._path is None or not normalized_user_id or normalized_limit == 0:
            return []

        with self._lock:
            document = self._load_document()
            records = self._records_for_user(document, normalized_user_id)
        records.sort(key=lambda item: item.created_at, reverse=True)
        return records[:normalized_limit]

    def _load_document(self) -> dict[str, Any]:
        if self._path is None or not self._path.exists():
            return {"version": 2, "backend": "file", "users": {}}

        try:
            raw = self._path.read_text(encoding="utf-8")
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                decoded.setdefault("version", 2)
                decoded.setdefault("backend", "file")
                decoded.setdefault("users", {})
                return decoded
        except Exception:
            pass

        return {"version": 2, "backend": "file", "users": {}}

    def _save_document(self, document: dict[str, Any]) -> None:
        if self._path is None:
            return

        document["version"] = 2
        document["backend"] = "file"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(document, ensure_ascii=True, separators=(",", ":"))
        self._path.write_text(encoded, encoding="utf-8")

    def _ensure_users(self, document: dict[str, Any]) -> dict[str, Any]:
        users = document.setdefault("users", {})
        if not isinstance(users, dict):
            users = {}
            document["users"] = users
        return users

    def _records_for_user(self, document: dict[str, Any], user_id: str) -> list[MemoryRecord]:
        users = self._ensure_users(document)
        records = users.get(user_id, [])
        if not isinstance(records, list):
            return []

        normalized: list[MemoryRecord] = []
        changed = False
        for item in records:
            if not isinstance(item, dict):
                changed = True
                continue

            record = self._record_from_dict(user_id, item)
            if record is None:
                changed = True
                continue

            normalized.append(record)
            if record.to_dict() != item:
                changed = True

        if changed:
            users[user_id] = [item.to_dict() for item in normalized]
            self._save_document(document)

        return normalized

    def _record_from_dict(self, user_id: str, item: dict[str, Any]) -> MemoryRecord | None:
        normalized_user_id = _normalize_user_id(str(item.get("user_id", user_id)))
        if normalized_user_id != user_id:
            normalized_user_id = user_id

        content = str(item.get("content", "")).strip()
        summary = str(item.get("summary", "")).strip()

        # 旧文件格式没有 content/source_task_id/importance，这里在读取时做一次懒迁移。
        if not content:
            prompt = str(item.get("prompt", "")).strip()
            preview = str(item.get("final_response_preview", "")).strip()
            content = "\n".join(part for part in (prompt, preview) if part)

        if not content and not summary:
            return None

        memory_id = str(item.get("memory_id", "")).strip() or f"mem_{uuid.uuid4().hex}"
        source_task_id = str(item.get("source_task_id", "")).strip()
        created_at = _read_int(item.get("created_at"), default_value=0)
        if created_at <= 0:
            created_at = _read_int(item.get("created_at_unix_ms"), default_value=0)
        if created_at <= 0:
            created_at = int(time.time() * 1000)

        importance = _read_float(item.get("importance"), default_value=-1.0)
        if importance < 0.0:
            importance = _read_float(item.get("estimated_success"), default_value=0.5)

        return MemoryRecord(
            memory_id=memory_id,
            user_id=normalized_user_id,
            content=content[:4000],
            summary=summary[:1000],
            source_task_id=source_task_id[:160],
            importance=_clamp_importance(importance),
            created_at=created_at,
        )

    def _normalize_record(self, record: MemoryRecord) -> MemoryRecord | None:
        user_id = _normalize_user_id(record.user_id)
        content = " ".join(record.content.strip().split())
        summary = " ".join(record.summary.strip().split())
        if not user_id or (not content and not summary):
            return None

        return MemoryRecord(
            memory_id=record.memory_id.strip() or f"mem_{uuid.uuid4().hex}",
            user_id=user_id,
            content=content[:4000],
            summary=summary[:1000],
            source_task_id=record.source_task_id.strip()[:160],
            importance=_clamp_importance(record.importance),
            created_at=record.created_at if record.created_at > 0 else int(time.time() * 1000),
        )


class VectorMemoryStore:
    """未来向量后端的接口骨架。

    这里故意不接具体向量库，先固定 runtime/API 所依赖的方法签名。后续实现时只需要在
    `memory_write` 内写入向量，在 `memory_recall` 内返回按相似度排序的 MemoryRecallHit。
    """

    def memory_write(self, record: MemoryRecord) -> MemoryRecord | None:
        raise NotImplementedError("vector memory backend is not implemented")

    def memory_recall(self, user_id: str, query: str, limit: int) -> list[MemoryRecallHit]:
        raise NotImplementedError("vector memory backend is not implemented")

    def memory_delete(self, user_id: str, memory_id: str) -> bool:
        raise NotImplementedError("vector memory backend is not implemented")

    def memory_list(self, user_id: str, limit: int) -> list[MemoryRecord]:
        raise NotImplementedError("vector memory backend is not implemented")


def _normalize_user_id(value: str) -> str:
    return value.strip().lower()


def _tokenize(text: str) -> set[str]:
    normalized = text.strip().lower()
    if not normalized:
        return set()
    return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", normalized))


def _clamp_importance(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


def _read_int(value: Any, default_value: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default_value


def _read_float(value: Any, default_value: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default_value
