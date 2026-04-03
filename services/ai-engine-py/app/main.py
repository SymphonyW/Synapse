import asyncio
import logging

import grpc

from app.config import load_config
from app.runtime import AgentRuntime
from app.service import AgentRuntimeService
from synapse.v1 import agent_pb2_grpc


async def serve() -> None:
    # 从环境变量加载 Runtime 与模型提供方配置。
    config = load_config()

    # Runtime 封装不同 provider 的 token 生成逻辑。
    runtime = AgentRuntime(
        model_provider=config.model_provider,
        model_provider_alias=config.model_provider_alias,
        openai_api_key=config.openai_api_key,
        openai_base_url=config.openai_base_url,
        openai_model=config.openai_model,
        openai_temperature=config.openai_temperature,
        openai_max_tokens=config.openai_max_tokens,
        openai_http_timeout_seconds=config.openai_http_timeout_seconds,
        openai_max_retries=config.openai_max_retries,
        openai_retry_backoff_seconds=config.openai_retry_backoff_seconds,
        agent_enabled_default=config.agent_enabled_default,
        agent_max_plan_steps=config.agent_max_plan_steps,
        agent_require_approval_for_high_risk=config.agent_require_approval_for_high_risk,
        agent_memory_file=config.agent_memory_file,
        agent_memory_max_entries_per_user=config.agent_memory_max_entries_per_user,
        agent_memory_recall_limit=config.agent_memory_recall_limit,
        agent_tool_http_allowlist=config.agent_tool_http_allowlist,
        agent_tool_http_timeout_seconds=config.agent_tool_http_timeout_seconds,
        agent_enable_code_execution=config.agent_enable_code_execution,
        agent_tool_policy_json=config.agent_tool_policy_json,
        agent_tool_audit_log_file=config.agent_tool_audit_log_file,
    )

    # 启动异步 gRPC 服务并注册 AgentRuntime 服务实现。
    server = grpc.aio.server()
    agent_pb2_grpc.add_AgentRuntimeServicer_to_server(AgentRuntimeService(runtime), server)
    server.add_insecure_port(config.bind_addr)

    # 持续阻塞，直到外部终止信号到来。
    await server.start()
    logging.info("ai engine listening on %s", config.bind_addr)
    await server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(serve())
