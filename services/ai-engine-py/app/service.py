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
            # 阶段 2：按 provider 输出逐 token 下发。
            async for token in self._runtime.run_prompt(request.prompt):
                yield agent_pb2.AgentEvent(
                    type=agent_pb2.AGENT_EVENT_TYPE_TOKEN,
                    token=token,
                    trace_id=trace_id,
                    emitted_at_unix_ms=int(time.time() * 1000),
                )

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
