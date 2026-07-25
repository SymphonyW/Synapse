import asyncio
from typing import AsyncIterator, Iterator

from app.providers.base import ModelCompletionResult, ModelStreamItem


class MockModelProvider:
    provider_name = "mock"

    def __init__(self, chunk_size: int = 12, stream_delay_seconds: float = 0.04) -> None:
        self._chunk_size = max(1, chunk_size)
        self._stream_delay_seconds = max(0.0, stream_delay_seconds)

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        long_form: bool = False,
    ) -> AsyncIterator[ModelStreamItem]:
        _ = long_form
        for chunk in self._chunk_text(self._build_response(_last_user_message(messages))):
            if self._stream_delay_seconds:
                await asyncio.sleep(self._stream_delay_seconds)
            yield ModelStreamItem(content=chunk)

    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> ModelCompletionResult:
        return ModelCompletionResult(content=self._build_response(_last_user_message(messages)))

    def _build_response(self, prompt: str) -> str:
        normalized_prompt = " ".join(prompt.strip().split())
        if not normalized_prompt:
            normalized_prompt = "empty request"

        return (
            "Synapse acknowledged your request: "
            f"{normalized_prompt}. "
            "Next milestone is replacing this mock runtime with real model routing."
        )

    def _chunk_text(self, text: str) -> Iterator[str]:
        if not text.strip():
            return

        for start in range(0, len(text), self._chunk_size):
            yield text[start : start + self._chunk_size]


def _last_user_message(messages: list[dict[str, str]]) -> str:
    for item in reversed(messages):
        if item.get("role") == "user":
            return str(item.get("content", ""))
    if messages:
        return str(messages[-1].get("content", ""))
    return ""
