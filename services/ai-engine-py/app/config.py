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


def load_config() -> Config:
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
    )
