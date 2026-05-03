import time
import uuid
from typing import AsyncIterator

from app.runtime import AgentRuntime
from synapse.v1 import agent_pb2, agent_pb2_grpc


class AgentRuntimeService(agent_pb2_grpc.AgentRuntimeServicer):
    """gRPC 服务门面：把 Runtime 输出转换为 AgentEvent 事件流。"""

    def __init__(self, runtime: AgentRuntime) -> None:
        self._runtime = runtime

    async def Health(self, request: agent_pb2.HealthRequest, context) -> agent_pb2.HealthResponse:
        # 从 Runtime 视角返回 provider 连接状态。
        return agent_pb2.HealthResponse(
            status="ok", model_provider=self._runtime.model_provider_display
        )

    async def MemoryWrite(
        self, request: agent_pb2.MemoryWriteRequest, context
    ) -> agent_pb2.MemoryWriteResponse:
        # 管理 API 走同一个 MemoryStore，避免手工写入和 Agent 自动写入出现两套文件格式。
        record = self._runtime.memory_write(
            user_id=request.user_id,
            content=request.content,
            summary=request.summary,
            source_task_id=request.source_task_id,
            importance=request.importance,
        )
        return agent_pb2.MemoryWriteResponse(record=_memory_record_to_proto(record or {}))

    async def MemoryRecall(
        self, request: agent_pb2.MemoryRecallRequest, context
    ) -> agent_pb2.MemoryRecallResponse:
        hits = self._runtime.memory_recall(
            user_id=request.user_id,
            query=request.query,
            limit=request.limit,
        )
        return agent_pb2.MemoryRecallResponse(
            hits=[_memory_hit_to_proto(hit) for hit in hits]
        )

    async def MemoryDelete(
        self, request: agent_pb2.MemoryDeleteRequest, context
    ) -> agent_pb2.MemoryDeleteResponse:
        deleted = self._runtime.memory_delete(
            user_id=request.user_id,
            memory_id=request.memory_id,
        )
        return agent_pb2.MemoryDeleteResponse(deleted=deleted)

    async def MemoryList(
        self, request: agent_pb2.MemoryListRequest, context
    ) -> agent_pb2.MemoryListResponse:
        items = self._runtime.memory_list(
            user_id=request.user_id,
            limit=request.limit,
        )
        return agent_pb2.MemoryListResponse(
            items=[_memory_record_to_proto(item) for item in items]
        )

    async def SubmitTask(
        self, request: agent_pb2.SubmitTaskRequest, context
    ) -> AsyncIterator[agent_pb2.AgentEvent]:
        # 同一任务流复用同一个 trace_id，便于端到端追踪。
        trace_id = str(uuid.uuid4())

        # 阶段 1：发送 started 事件。
        yield agent_pb2.AgentEvent(
            type=agent_pb2.AGENT_EVENT_TYPE_STARTED,
            message=f"task {request.task_id} started",
            trace_id=trace_id,
            emitted_at_unix_ms=int(time.time() * 1000),
        )

        try:
            # 阶段 2：按运行时事件流下发 info/token。
            paused = False
            async for runtime_event in self._runtime.run_task(
                request.task_id,
                request.user_id,
                request.prompt,
                dict(request.metadata),
            ):
                if runtime_event.kind == "token":
                    yield agent_pb2.AgentEvent(
                        type=agent_pb2.AGENT_EVENT_TYPE_TOKEN,
                        token=runtime_event.token,
                        trace_id=trace_id,
                        emitted_at_unix_ms=int(time.time() * 1000),
                    )
                    continue

                if runtime_event.kind == "info":
                    yield agent_pb2.AgentEvent(
                        type=agent_pb2.AGENT_EVENT_TYPE_INFO,
                        message=runtime_event.message,
                        trace_id=trace_id,
                        emitted_at_unix_ms=int(time.time() * 1000),
                    )
                    continue

                if runtime_event.kind == "pause":
                    paused = True
                    if runtime_event.message:
                        yield agent_pb2.AgentEvent(
                            type=agent_pb2.AGENT_EVENT_TYPE_INFO,
                            message=runtime_event.message,
                            trace_id=trace_id,
                            emitted_at_unix_ms=int(time.time() * 1000),
                        )
                    break

            if paused:
                return

            # 阶段 3：token 流正常结束后发送 completed。
            yield agent_pb2.AgentEvent(
                type=agent_pb2.AGENT_EVENT_TYPE_COMPLETED,
                message=f"task {request.task_id} completed",
                trace_id=trace_id,
                emitted_at_unix_ms=int(time.time() * 1000),
            )
        except Exception as exc:
            # Runtime 异常统一转换为 FAILED 事件，便于 gateway 一致地执行重试/死信逻辑。
            yield agent_pb2.AgentEvent(
                type=agent_pb2.AGENT_EVENT_TYPE_FAILED,
                message=str(exc),
                trace_id=trace_id,
                emitted_at_unix_ms=int(time.time() * 1000),
            )


def _memory_record_to_proto(record: dict) -> agent_pb2.MemoryRecord:
    return agent_pb2.MemoryRecord(
        memory_id=str(record.get("memory_id", "")),
        user_id=str(record.get("user_id", "")),
        content=str(record.get("content", "")),
        summary=str(record.get("summary", "")),
        source_task_id=str(record.get("source_task_id", "")),
        importance=float(record.get("importance", 0.0) or 0.0),
        created_at=int(record.get("created_at", 0) or 0),
    )


def _memory_hit_to_proto(hit: dict) -> agent_pb2.MemoryRecallHit:
    return agent_pb2.MemoryRecallHit(
        record=_memory_record_to_proto(hit),
        score=float(hit.get("score", 0.0) or 0.0),
        matched_terms=[str(item) for item in hit.get("matched_terms", [])],
    )
