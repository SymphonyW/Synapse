import asyncio
import json
import threading
import time
from typing import Any, AsyncIterator, Iterator
from urllib import error as urllib_error
from urllib import request as urllib_request

from app.providers.base import (
    ModelCompletionResult,
    ModelProviderError,
    ModelStreamItem,
)


class OpenAICompatibleProvider:
    provider_name = "openai"

    def __init__(
        self,
        api_key: str,
        base_url: str = "",
        model: str = "gpt-4o-mini",
        temperature: float = 0.2,
        max_tokens: int = 512,
        http_timeout_seconds: float = 45.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.5,
    ) -> None:
        if not api_key:
            raise ValueError(
                "SYNAPSE_OPENAI_API_KEY is required when SYNAPSE_MODEL_PROVIDER=openai"
            )
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._http_timeout_seconds = max(5.0, http_timeout_seconds)
        self._max_retries = max(1, max_retries)
        self._retry_backoff_seconds = max(0.2, retry_backoff_seconds)

    @property
    def endpoint(self) -> str:
        endpoint = self._base_url.strip() or "https://api.openai.com/v1"
        return endpoint.rstrip("/") + "/chat/completions"

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        long_form: bool = False,
    ) -> AsyncIterator[ModelStreamItem]:
        _ = long_form
        queue: asyncio.Queue[object] = asyncio.Queue()
        sentinel = object()
        loop = asyncio.get_running_loop()

        def push(item: object) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        def worker() -> None:
            try:
                for item in self.stream_sync(messages):
                    if item.content or item.finish_reason:
                        push(item)
            except Exception as exc:
                push(exc)
            finally:
                push(sentinel)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = await queue.get()
            if item is sentinel:
                return
            if isinstance(item, Exception):
                raise item
            if isinstance(item, ModelStreamItem):
                yield item

    async def complete(
        self,
        messages: list[dict[str, str]],
    ) -> ModelCompletionResult:
        return await asyncio.to_thread(self.complete_sync, messages)

    def stream_sync(self, messages: list[dict[str, str]]) -> Iterator[ModelStreamItem]:
        payload = self._build_payload(messages, stream=True)
        data = json.dumps(payload).encode("utf-8")
        retryable_http_status = {429, 500, 502, 503, 504}
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            request = urllib_request.Request(
                self.endpoint,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "text/event-stream",
                },
                method="POST",
            )

            emitted_any = False
            try:
                with urllib_request.urlopen(request, timeout=self._http_timeout_seconds) as response:
                    for item in self._iter_sse_items(response):
                        emitted_any = True
                        yield item
                    return
            except urllib_error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="ignore")
                retryable = exc.code in retryable_http_status
                if emitted_any or not retryable or attempt >= self._max_retries:
                    raise ModelProviderError(
                        f"openai stream request failed: HTTP {exc.code} {body}",
                        retryable=retryable and not emitted_any,
                        status_code=exc.code,
                        code="http_error",
                        raw_error=exc,
                    ) from exc

                retry_after_header = exc.headers.get("Retry-After") if exc.headers else None
                time.sleep(self._compute_retry_delay(attempt, retry_after_header))
                last_error = exc
            except urllib_error.URLError as exc:
                if emitted_any or attempt >= self._max_retries:
                    raise ModelProviderError(
                        f"openai stream request failed: {exc.reason}",
                        retryable=not emitted_any,
                        code="network_error",
                        raw_error=exc,
                    ) from exc

                time.sleep(self._compute_retry_delay(attempt, None))
                last_error = exc

        if last_error is not None:
            raise ModelProviderError(
                f"openai stream request failed: {last_error}",
                retryable=True,
                code="retry_exhausted",
                raw_error=last_error,
            )
        raise ModelProviderError("openai stream request failed: unknown error")

    def complete_sync(self, messages: list[dict[str, str]]) -> ModelCompletionResult:
        payload = self._build_payload(messages, stream=False)
        data = json.dumps(payload).encode("utf-8")
        response_payload = self._perform_request_with_retry(data)
        return self._extract_completion_result(response_payload)

    def _build_payload(self, messages: list[dict[str, str]], stream: bool) -> dict[str, Any]:
        return {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": stream,
        }

    def _iter_sse_items(self, response: Any) -> Iterator[ModelStreamItem]:
        while True:
            raw_line = response.readline()
            if not raw_line:
                return

            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue

            payload_raw = line[5:].strip()
            if payload_raw == "[DONE]":
                return

            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                continue

            if isinstance(payload, dict) and payload.get("error"):
                raise ModelProviderError(f"openai stream request failed: {payload['error']}")

            item = self._extract_stream_item(payload)
            if item.content or item.finish_reason:
                yield item

    def _extract_stream_item(self, payload: Any) -> ModelStreamItem:
        if not isinstance(payload, dict):
            return ModelStreamItem()

        choices = payload.get("choices")
        if not isinstance(choices, list):
            return ModelStreamItem()

        fragments: list[str] = []
        finish_reason = ""
        for choice in choices:
            if not isinstance(choice, dict):
                continue

            raw_finish_reason = choice.get("finish_reason")
            if isinstance(raw_finish_reason, str) and raw_finish_reason.strip():
                finish_reason = raw_finish_reason.strip()

            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue

            chunk = self._normalize_content(delta.get("content"))
            if chunk:
                fragments.append(chunk)

        return ModelStreamItem(content="".join(fragments), finish_reason=finish_reason)

    def _extract_completion_result(self, payload: dict[str, Any]) -> ModelCompletionResult:
        choices = payload.get("choices") or []
        if not choices:
            return ModelCompletionResult()

        choice = choices[0]
        finish_reason = ""
        raw_finish_reason = choice.get("finish_reason")
        if isinstance(raw_finish_reason, str):
            finish_reason = raw_finish_reason.strip()

        message = choice.get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        texts.append(text)
            content = "\n".join(texts)

        if isinstance(content, str):
            return ModelCompletionResult(
                content=content.strip(),
                finish_reason=finish_reason,
            )
        return ModelCompletionResult(finish_reason=finish_reason)

    def _normalize_content(self, value: Any) -> str:
        if isinstance(value, str):
            return value

        if isinstance(value, list):
            fragments: list[str] = []
            for item in value:
                if isinstance(item, str):
                    fragments.append(item)
                    continue

                if not isinstance(item, dict):
                    continue

                text = item.get("text")
                if isinstance(text, str):
                    fragments.append(text)
                    continue

                nested_content = item.get("content")
                if isinstance(nested_content, str):
                    fragments.append(nested_content)

            return "".join(fragments)

        return ""

    def _perform_request_with_retry(self, data: bytes) -> dict:
        retryable_http_status = {429, 500, 502, 503, 504}
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            request = urllib_request.Request(
                self.endpoint,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                method="POST",
            )

            try:
                with urllib_request.urlopen(request, timeout=self._http_timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib_error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="ignore")
                retryable = exc.code in retryable_http_status
                if not retryable or attempt >= self._max_retries:
                    raise ModelProviderError(
                        f"openai request failed: HTTP {exc.code} {body}",
                        retryable=retryable,
                        status_code=exc.code,
                        code="http_error",
                        raw_error=exc,
                    ) from exc

                retry_after_header = exc.headers.get("Retry-After") if exc.headers else None
                time.sleep(self._compute_retry_delay(attempt, retry_after_header))
                last_error = exc
            except urllib_error.URLError as exc:
                if attempt >= self._max_retries:
                    raise ModelProviderError(
                        f"openai request failed: {exc.reason}",
                        retryable=True,
                        code="network_error",
                        raw_error=exc,
                    ) from exc

                time.sleep(self._compute_retry_delay(attempt, None))
                last_error = exc

        if last_error is not None:
            raise ModelProviderError(
                f"openai request failed: {last_error}",
                retryable=True,
                code="retry_exhausted",
                raw_error=last_error,
            )
        raise ModelProviderError("openai request failed: unknown error")

    def _compute_retry_delay(self, attempt: int, retry_after_header: str | None) -> float:
        if retry_after_header:
            try:
                parsed = float(retry_after_header)
                if parsed > 0:
                    return min(parsed, 20.0)
            except ValueError:
                pass

        return min(self._retry_backoff_seconds * attempt, 10.0)
