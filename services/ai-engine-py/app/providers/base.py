from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol


MODEL_MESSAGES_METADATA_KEY = "model_messages_json"


@dataclass(frozen=True)
class ModelStreamItem:
    content: str = ""
    finish_reason: str = ""


@dataclass(frozen=True)
class ModelCompletionResult:
    content: str = ""
    finish_reason: str = ""


class ModelProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        code: str = "provider_error",
        raw_error: Any = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.code = code
        self.raw_error = raw_error


class ModelProvider(Protocol):
    provider_name: str

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        long_form: bool = False,
    ) -> AsyncIterator[ModelStreamItem]:
        ...

    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> ModelCompletionResult:
        ...
