import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # gRPC 监听地址，例如容器内常用 0.0.0.0:50051。
    bind_addr: str
    # Runtime 提供方开关：mock 或 openai。
    model_provider: str
    # 可选显示别名，用于 health 返回的 model_provider 文案。
    model_provider_alias: str
    # OpenAI 兼容提供方参数。
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    openai_temperature: float
    openai_max_tokens: int
    openai_http_timeout_seconds: float
    openai_max_retries: int
    openai_retry_backoff_seconds: float
    openai_continuation_max_rounds: int
    openai_long_form_min_chars: int
    # Agent runtime controls.
    agent_enabled_default: bool
    agent_max_plan_steps: int
    agent_generation_timeout_seconds: float
    agent_stream_idle_timeout_seconds: float
    agent_require_approval_for_high_risk: bool
    memory_backend: str
    agent_memory_file: str
    agent_memory_max_entries_per_user: int
    agent_memory_recall_limit: int
    vector_database_url: str
    vector_embedding_provider: str
    vector_embedding_model: str
    vector_embedding_base_url: str
    vector_embedding_api_key: str
    vector_embedding_dimension: int
    vector_memory_top_k: int
    agent_tool_http_allowlist: tuple[str, ...]
    agent_tool_http_timeout_seconds: float
    agent_enable_code_execution: bool
    agent_tool_policy_json: str
    agent_tool_audit_log_file: str
    openapi_enabled: bool
    openapi_spec_file: str
    openapi_base_url_override: str
    openapi_static_headers: dict[str, str]
    openapi_bearer_token: str
    openapi_api_key_header: str
    openapi_api_key_value: str
    openapi_http_timeout_seconds: float
    openapi_max_response_bytes: int
    openapi_allowed_schemes: tuple[str, ...]
    mcp_stdio_enabled: bool
    mcp_stdio_command: str
    mcp_stdio_args: tuple[str, ...]
    mcp_stdio_env: dict[str, str]
    mcp_stdio_workdir: str
    mcp_stdio_timeout_seconds: float
    mcp_tool_name_prefix: str


def _read_float(value: str, default: float) -> float:
    # 对异常环境变量值保持容错，回退到默认值。
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_int(value: str, default: int) -> int:
    # 对异常环境变量值保持容错，回退到默认值。
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_bool(value: str, default: bool) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on", "y"}:
        return True
    if normalized in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _read_csv_tuple(value: str) -> tuple[str, ...]:
    items = [segment.strip().lower() for segment in (value or "").split(",")]
    normalized = [segment for segment in items if segment]
    return tuple(normalized)


def _read_json_string_tuple(value: str, env_name: str) -> tuple[str, ...]:
    raw = (value or "").strip()
    if not raw:
        return ()

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{env_name} must be a JSON array of strings") from exc

    if not isinstance(decoded, list):
        raise ValueError(f"{env_name} must be a JSON array of strings")

    items: list[str] = []
    for item in decoded:
        if not isinstance(item, str):
            raise ValueError(f"{env_name} must be a JSON array of strings")
        items.append(item)
    return tuple(items)


def _read_json_env(value: str, env_name: str) -> dict[str, str]:
    raw = (value or "").strip()
    if not raw:
        return {}

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{env_name} must be a JSON object") from exc

    if not isinstance(decoded, dict):
        raise ValueError(f"{env_name} must be a JSON object")

    env: dict[str, str] = {}
    for key, item in decoded.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            raise ValueError(f"{env_name} keys must not be empty")
        if isinstance(item, (dict, list)):
            raise ValueError(f"{env_name} values must be scalar values")
        env[normalized_key] = "" if item is None else str(item)
    return env


def load_config() -> Config:
    memory_backend = os.getenv("SYNAPSE_MEMORY_BACKEND", "file").strip().lower() or "file"
    if memory_backend not in {"file", "vector"}:
        raise ValueError("SYNAPSE_MEMORY_BACKEND must be one of: file, vector")

    vector_database_url = os.getenv("SYNAPSE_VECTOR_DATABASE_URL", "").strip()
    vector_embedding_provider = os.getenv("SYNAPSE_VECTOR_EMBEDDING_PROVIDER", "").strip().lower()
    vector_embedding_model = os.getenv("SYNAPSE_VECTOR_EMBEDDING_MODEL", "").strip()
    vector_embedding_base_url = os.getenv("SYNAPSE_VECTOR_EMBEDDING_BASE_URL", "").strip()
    vector_embedding_api_key = os.getenv("SYNAPSE_VECTOR_EMBEDDING_API_KEY", "").strip()
    vector_embedding_dimension = _read_int(
        os.getenv("SYNAPSE_VECTOR_EMBEDDING_DIMENSION", "0"),
        0,
    )
    vector_memory_top_k = _read_int(os.getenv("SYNAPSE_VECTOR_MEMORY_TOP_K", "10"), 10)

    if memory_backend == "vector":
        if not vector_database_url:
            raise ValueError(
                "SYNAPSE_VECTOR_DATABASE_URL is required when SYNAPSE_MEMORY_BACKEND=vector"
            )
        if not vector_embedding_provider:
            raise ValueError(
                "SYNAPSE_VECTOR_EMBEDDING_PROVIDER is required when SYNAPSE_MEMORY_BACKEND=vector"
            )
        if vector_embedding_provider != "openai_compatible":
            raise ValueError(
                "SYNAPSE_VECTOR_EMBEDDING_PROVIDER currently supports only openai_compatible"
            )
        if not vector_embedding_model:
            raise ValueError(
                "SYNAPSE_VECTOR_EMBEDDING_MODEL is required when SYNAPSE_MEMORY_BACKEND=vector"
            )
        if not vector_embedding_api_key:
            raise ValueError(
                "SYNAPSE_VECTOR_EMBEDDING_API_KEY is required when SYNAPSE_MEMORY_BACKEND=vector"
            )
        if vector_embedding_dimension <= 0:
            raise ValueError(
                "SYNAPSE_VECTOR_EMBEDDING_DIMENSION must be > 0 when SYNAPSE_MEMORY_BACKEND=vector"
            )
        if vector_memory_top_k <= 0:
            raise ValueError(
                "SYNAPSE_VECTOR_MEMORY_TOP_K must be > 0 when SYNAPSE_MEMORY_BACKEND=vector"
            )

    openapi_enabled = _read_bool(os.getenv("SYNAPSE_OPENAPI_ENABLED", "false"), False)
    openapi_spec_file = os.getenv("SYNAPSE_OPENAPI_SPEC_FILE", "").strip()
    openapi_base_url_override = ""
    openapi_static_headers: dict[str, str] = {}
    openapi_bearer_token = ""
    openapi_api_key_header = ""
    openapi_api_key_value = ""
    openapi_http_timeout_seconds = 12.0
    openapi_max_response_bytes = 65536
    openapi_allowed_schemes = ("http", "https")

    if openapi_enabled:
        if not openapi_spec_file:
            raise ValueError(
                "SYNAPSE_OPENAPI_SPEC_FILE is required when SYNAPSE_OPENAPI_ENABLED=true"
            )
        openapi_base_url_override = os.getenv("SYNAPSE_OPENAPI_BASE_URL_OVERRIDE", "").strip()
        openapi_static_headers = _read_json_env(
            os.getenv("SYNAPSE_OPENAPI_STATIC_HEADERS_JSON", "{}"),
            "SYNAPSE_OPENAPI_STATIC_HEADERS_JSON",
        )
        openapi_bearer_token = os.getenv("SYNAPSE_OPENAPI_BEARER_TOKEN", "").strip()
        openapi_api_key_header = os.getenv("SYNAPSE_OPENAPI_API_KEY_HEADER", "").strip()
        openapi_api_key_value = os.getenv("SYNAPSE_OPENAPI_API_KEY_VALUE", "").strip()
        openapi_http_timeout_seconds = _read_float(
            os.getenv("SYNAPSE_OPENAPI_HTTP_TIMEOUT_SECONDS", "12"),
            12.0,
        )
        openapi_max_response_bytes = _read_int(
            os.getenv("SYNAPSE_OPENAPI_MAX_RESPONSE_BYTES", "65536"),
            65536,
        )
        openapi_allowed_schemes = _read_csv_tuple(
            os.getenv("SYNAPSE_OPENAPI_ALLOWED_SCHEMES", "http,https")
        )

    mcp_stdio_enabled = _read_bool(os.getenv("SYNAPSE_MCP_STDIO_ENABLED", "false"), False)
    mcp_stdio_command = os.getenv("SYNAPSE_MCP_STDIO_COMMAND", "").strip()
    mcp_stdio_args: tuple[str, ...] = ()
    mcp_stdio_env: dict[str, str] = {}
    mcp_stdio_workdir = ""
    mcp_stdio_timeout_seconds = 10.0
    mcp_tool_name_prefix = os.getenv("SYNAPSE_MCP_TOOL_NAME_PREFIX", "mcp").strip() or "mcp"

    if mcp_stdio_enabled:
        if not mcp_stdio_command:
            raise ValueError(
                "SYNAPSE_MCP_STDIO_COMMAND is required when SYNAPSE_MCP_STDIO_ENABLED=true"
            )
        mcp_stdio_args = _read_json_string_tuple(
            os.getenv("SYNAPSE_MCP_STDIO_ARGS_JSON", "[]"),
            "SYNAPSE_MCP_STDIO_ARGS_JSON",
        )
        mcp_stdio_env = _read_json_env(
            os.getenv("SYNAPSE_MCP_STDIO_ENV_JSON", "{}"),
            "SYNAPSE_MCP_STDIO_ENV_JSON",
        )
        mcp_stdio_workdir = os.getenv("SYNAPSE_MCP_STDIO_WORKDIR", "").strip()
        mcp_stdio_timeout_seconds = _read_float(
            os.getenv("SYNAPSE_MCP_STDIO_TIMEOUT_SECONDS", "10"),
            10.0,
        )

    # 统一读取环境变量，保持启动流程简洁。
    return Config(
        bind_addr=os.getenv("SYNAPSE_AI_BIND_ADDR", "0.0.0.0:50051"),
        model_provider=os.getenv("SYNAPSE_MODEL_PROVIDER", "mock"),
        model_provider_alias=os.getenv("SYNAPSE_MODEL_PROVIDER_ALIAS", ""),
        openai_api_key=os.getenv("SYNAPSE_OPENAI_API_KEY", ""),
        openai_base_url=os.getenv("SYNAPSE_OPENAI_BASE_URL", ""),
        openai_model=os.getenv("SYNAPSE_OPENAI_MODEL", "gpt-4o-mini"),
        openai_temperature=_read_float(os.getenv("SYNAPSE_OPENAI_TEMPERATURE", "0.2"), 0.2),
        openai_max_tokens=_read_int(os.getenv("SYNAPSE_OPENAI_MAX_TOKENS", "512"), 512),
        openai_http_timeout_seconds=_read_float(
            os.getenv("SYNAPSE_OPENAI_HTTP_TIMEOUT_SECONDS", "45"), 45.0
        ),
        openai_max_retries=_read_int(os.getenv("SYNAPSE_OPENAI_MAX_RETRIES", "3"), 3),
        openai_retry_backoff_seconds=_read_float(
            os.getenv("SYNAPSE_OPENAI_RETRY_BACKOFF_SECONDS", "1.5"), 1.5
        ),
        openai_continuation_max_rounds=_read_int(
            os.getenv("SYNAPSE_OPENAI_CONTINUATION_MAX_ROUNDS", "8"), 8
        ),
        openai_long_form_min_chars=_read_int(
            os.getenv("SYNAPSE_OPENAI_LONG_FORM_MIN_CHARS", "2400"), 2400
        ),
        agent_enabled_default=_read_bool(
            os.getenv("SYNAPSE_AGENT_ENABLED_DEFAULT", "true"), True
        ),
        agent_max_plan_steps=_read_int(os.getenv("SYNAPSE_AGENT_MAX_PLAN_STEPS", "6"), 6),
        agent_generation_timeout_seconds=_read_float(
            os.getenv("SYNAPSE_AGENT_GENERATION_TIMEOUT_SECONDS", "30"), 30.0
        ),
        agent_stream_idle_timeout_seconds=_read_float(
            os.getenv("SYNAPSE_AGENT_STREAM_IDLE_TIMEOUT_SECONDS", "15"), 15.0
        ),
        agent_require_approval_for_high_risk=_read_bool(
            os.getenv("SYNAPSE_AGENT_REQUIRE_APPROVAL_FOR_HIGH_RISK", "true"), True
        ),
        memory_backend=memory_backend,
        agent_memory_file=os.getenv("SYNAPSE_AGENT_MEMORY_FILE", "/tmp/synapse-agent-memory.json"),
        agent_memory_max_entries_per_user=_read_int(
            os.getenv("SYNAPSE_AGENT_MEMORY_MAX_ENTRIES_PER_USER", "80"), 80
        ),
        agent_memory_recall_limit=_read_int(
            os.getenv("SYNAPSE_AGENT_MEMORY_RECALL_LIMIT", "3"), 3
        ),
        vector_database_url=vector_database_url,
        vector_embedding_provider=vector_embedding_provider,
        vector_embedding_model=vector_embedding_model,
        vector_embedding_base_url=vector_embedding_base_url,
        vector_embedding_api_key=vector_embedding_api_key,
        vector_embedding_dimension=vector_embedding_dimension,
        vector_memory_top_k=vector_memory_top_k,
        agent_tool_http_allowlist=_read_csv_tuple(
            os.getenv("SYNAPSE_AGENT_TOOL_HTTP_ALLOWLIST", "")
        ),
        agent_tool_http_timeout_seconds=_read_float(
            os.getenv("SYNAPSE_AGENT_TOOL_HTTP_TIMEOUT_SECONDS", "12"), 12.0
        ),
        agent_enable_code_execution=_read_bool(
            os.getenv("SYNAPSE_AGENT_ENABLE_CODE_EXECUTION", "false"), False
        ),
        agent_tool_policy_json=os.getenv("SYNAPSE_AGENT_TOOL_POLICY_JSON", ""),
        agent_tool_audit_log_file=os.getenv(
            "SYNAPSE_AGENT_TOOL_AUDIT_LOG_FILE",
            "/tmp/synapse-agent-tool-audit.log",
        ),
        openapi_enabled=openapi_enabled,
        openapi_spec_file=openapi_spec_file,
        openapi_base_url_override=openapi_base_url_override,
        openapi_static_headers=openapi_static_headers,
        openapi_bearer_token=openapi_bearer_token,
        openapi_api_key_header=openapi_api_key_header,
        openapi_api_key_value=openapi_api_key_value,
        openapi_http_timeout_seconds=openapi_http_timeout_seconds,
        openapi_max_response_bytes=openapi_max_response_bytes,
        openapi_allowed_schemes=openapi_allowed_schemes,
        mcp_stdio_enabled=mcp_stdio_enabled,
        mcp_stdio_command=mcp_stdio_command,
        mcp_stdio_args=mcp_stdio_args,
        mcp_stdio_env=mcp_stdio_env,
        mcp_stdio_workdir=mcp_stdio_workdir,
        mcp_stdio_timeout_seconds=mcp_stdio_timeout_seconds,
        mcp_tool_name_prefix=mcp_tool_name_prefix,
    )
