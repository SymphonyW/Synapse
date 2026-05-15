import asyncio
import json
import logging
import pathlib
from typing import Any

import grpc

from app.config import load_config
from app.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.memory import FileMemoryStore, MemoryStore
from app.runtime import AgentRuntime
from app.service import AgentRuntimeService
from app.tools import MCPToolProvider, OpenAPIHTTPExecutor, OpenAPIToolProvider, StdioMCPAdapter
from app.vector_memory import PostgresVectorMemoryStore
from synapse.v1 import agent_pb2_grpc


def _load_openapi_spec_file(file_path: str) -> dict[str, Any]:
    path = pathlib.Path(file_path).expanduser()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"failed to read SYNAPSE_OPENAPI_SPEC_FILE {path}: {exc}") from exc

    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ValueError(
                    "YAML OpenAPI specs require PyYAML; use JSON or install PyYAML"
                ) from exc
            decoded = yaml.safe_load(raw)
        else:
            decoded = json.loads(raw)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"failed to parse OpenAPI spec file {path}: {exc}") from exc

    if not isinstance(decoded, dict):
        raise ValueError(f"OpenAPI spec file {path} must contain a JSON object")
    return decoded


def _build_memory_store(config) -> MemoryStore:
    if config.memory_backend == "file":
        return FileMemoryStore(
            file_path=config.agent_memory_file,
            max_entries_per_user=config.agent_memory_max_entries_per_user,
        )

    if config.memory_backend == "vector":
        provider = OpenAICompatibleEmbeddingProvider(
            model=config.vector_embedding_model,
            base_url=config.vector_embedding_base_url,
            api_key=config.vector_embedding_api_key,
        )
        return PostgresVectorMemoryStore(
            database_url=config.vector_database_url,
            embedding_provider=provider,
            embedding_dimension=config.vector_embedding_dimension,
            top_k=config.vector_memory_top_k,
            max_entries_per_user=config.agent_memory_max_entries_per_user,
        )

    raise ValueError(f"unsupported memory backend: {config.memory_backend}")


async def serve() -> None:
    # 从环境变量加载 Runtime 与模型提供方配置。
    config = load_config()
    agent_tool_providers = []
    if config.openapi_enabled:
        openapi_executor = OpenAPIHTTPExecutor(
            base_url_override=config.openapi_base_url_override,
            allowlist=config.agent_tool_http_allowlist,
            timeout_seconds=config.openapi_http_timeout_seconds,
            max_response_bytes=config.openapi_max_response_bytes,
            allowed_schemes=config.openapi_allowed_schemes,
            static_headers=config.openapi_static_headers,
            bearer_token=config.openapi_bearer_token,
            api_key_header=config.openapi_api_key_header,
            api_key_value=config.openapi_api_key_value,
        )
        agent_tool_providers.append(
            OpenAPIToolProvider(
                _load_openapi_spec_file(config.openapi_spec_file),
                executor=openapi_executor,
            )
        )
    if config.mcp_stdio_enabled:
        agent_tool_providers.append(
            MCPToolProvider(
                StdioMCPAdapter(
                    command=config.mcp_stdio_command,
                    args=config.mcp_stdio_args,
                    env=config.mcp_stdio_env,
                    working_dir=config.mcp_stdio_workdir,
                    timeout_seconds=config.mcp_stdio_timeout_seconds,
                ),
                name_prefix=config.mcp_tool_name_prefix,
            )
        )

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
        openai_continuation_max_rounds=config.openai_continuation_max_rounds,
        openai_long_form_min_chars=config.openai_long_form_min_chars,
        agent_enabled_default=config.agent_enabled_default,
        agent_max_plan_steps=config.agent_max_plan_steps,
        agent_generation_timeout_seconds=config.agent_generation_timeout_seconds,
        agent_stream_idle_timeout_seconds=config.agent_stream_idle_timeout_seconds,
        agent_require_approval_for_high_risk=config.agent_require_approval_for_high_risk,
        agent_memory_file=config.agent_memory_file,
        agent_memory_max_entries_per_user=config.agent_memory_max_entries_per_user,
        agent_memory_recall_limit=config.agent_memory_recall_limit,
        agent_memory_store=_build_memory_store(config),
        agent_tool_http_allowlist=config.agent_tool_http_allowlist,
        agent_tool_http_timeout_seconds=config.agent_tool_http_timeout_seconds,
        agent_enable_code_execution=config.agent_enable_code_execution,
        agent_tool_policy_json=config.agent_tool_policy_json,
        agent_tool_audit_log_file=config.agent_tool_audit_log_file,
        agent_tool_providers=tuple(agent_tool_providers),
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
