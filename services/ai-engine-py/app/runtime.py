import asyncio
import json
import threading
import time
from typing import Any, AsyncIterator, Iterator
from urllib import error as urllib_error
from urllib import request as urllib_request


MODEL_MESSAGES_METADATA_KEY = "model_messages_json"


class AgentRuntime:
    """面向 provider 的运行时，按提示词生成 token 流。"""

    def __init__(
        self,
        model_provider: str,
        model_provider_alias: str = "",
        openai_api_key: str = "",
        openai_base_url: str = "",
        openai_model: str = "gpt-4o-mini",
        openai_temperature: float = 0.2,
        openai_max_tokens: int = 512,
        openai_http_timeout_seconds: float = 45.0,
        openai_max_retries: int = 3,
        openai_retry_backoff_seconds: float = 1.5,
    ) -> None:
        raw_provider = model_provider.strip().lower() or "mock"
        alias = model_provider_alias.strip().lower()

        # Support semantic aliases while using the same OpenAI-compatible transport path.
        if raw_provider in {"zhipu", "gemini"}:
            alias = alias or raw_provider
            raw_provider = "openai"

        self.model_provider = raw_provider
        self.model_provider_display = alias or raw_provider
        self._openai_api_key = openai_api_key
        self._openai_base_url = openai_base_url
        self._openai_model = openai_model
        self._openai_temperature = openai_temperature
        self._openai_max_tokens = openai_max_tokens
        self._openai_http_timeout_seconds = max(5.0, openai_http_timeout_seconds)
        self._openai_max_retries = max(1, openai_max_retries)
        self._openai_retry_backoff_seconds = max(0.2, openai_retry_backoff_seconds)

        if self.model_provider == "openai":
            # OpenAI 模式必须显式配置 API Key。
            if not openai_api_key:
                raise ValueError(
                    "SYNAPSE_OPENAI_API_KEY is required when SYNAPSE_MODEL_PROVIDER=openai"
                )

        elif self.model_provider != "mock":
            raise ValueError(f"unsupported model provider: {self.model_provider}")

    async def run_prompt(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> AsyncIterator[str]:
        # provider 分流集中在此，保持 service 层与具体模型解耦。
        if self.model_provider == "openai":
            async for token in self._run_openai(prompt, metadata):
                yield token
            return

        async for token in self._run_mock(prompt):
            yield token

    async def _run_mock(self, prompt: str) -> AsyncIterator[str]:
        # Mock 模式按词流式输出，便于联调与集成测试。
        response = self._build_response(prompt)
        for chunk in self._chunk_text(response):
            await asyncio.sleep(0.04)
            yield chunk

    async def _run_openai(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> AsyncIterator[str]:
        normalized_prompt = " ".join(prompt.strip().split())
        if not normalized_prompt:
            normalized_prompt = "empty request"

        emitted_any = False
        try:
            async for chunk in self._request_openai_stream_async(
                normalized_prompt, metadata
            ):
                emitted_any = True
                yield chunk
        except Exception:
            if emitted_any:
                raise

            # 兼容不支持 stream=true 的 OpenAI 兼容网关，降级到普通 completions。
            response_text = await asyncio.to_thread(
                self._request_openai_completion, normalized_prompt, metadata
            )
            if not response_text:
                yield "(empty response)"
                return

            for chunk in self._chunk_text(response_text):
                yield chunk

    async def _request_openai_stream_async(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> AsyncIterator[str]:
        queue: asyncio.Queue[object] = asyncio.Queue()
        sentinel = object()
        loop = asyncio.get_running_loop()

        def push(item: object) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        def worker() -> None:
            try:
                for chunk in self._request_openai_stream_with_retry(prompt, metadata):
                    if chunk:
                        push(chunk)
            except Exception as exc:  # pragma: no cover - runtime safety branch
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
            yield str(item)

    def _request_openai_stream_with_retry(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> Iterator[str]:
        endpoint = self._openai_base_url.strip() or "https://api.openai.com/v1"
        endpoint = endpoint.rstrip("/") + "/chat/completions"
        payload = self._build_openai_payload(prompt, stream=True, metadata=metadata)
        data = json.dumps(payload).encode("utf-8")

        retryable_http_status = {429, 500, 502, 503, 504}
        last_error: Exception | None = None

        for attempt in range(1, self._openai_max_retries + 1):
            request = urllib_request.Request(
                endpoint,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._openai_api_key}",
                    "Accept": "text/event-stream",
                },
                method="POST",
            )

            emitted_any = False
            try:
                with urllib_request.urlopen(request, timeout=self._openai_http_timeout_seconds) as response:
                    for chunk in self._iter_openai_sse_chunks(response):
                        emitted_any = True
                        yield chunk
                    return
            except urllib_error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="ignore")
                if emitted_any or exc.code not in retryable_http_status or attempt >= self._openai_max_retries:
                    raise RuntimeError(f"openai stream request failed: HTTP {exc.code} {body}") from exc

                retry_after_header = exc.headers.get("Retry-After") if exc.headers else None
                time.sleep(self._compute_retry_delay(attempt, retry_after_header))
                last_error = exc
            except urllib_error.URLError as exc:
                if emitted_any or attempt >= self._openai_max_retries:
                    raise RuntimeError(f"openai stream request failed: {exc.reason}") from exc

                time.sleep(self._compute_retry_delay(attempt, None))
                last_error = exc

        if last_error is not None:
            raise RuntimeError(f"openai stream request failed: {last_error}")
        raise RuntimeError("openai stream request failed: unknown error")

    def _iter_openai_sse_chunks(self, response: Any) -> Iterator[str]:
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
                raise RuntimeError(f"openai stream request failed: {payload['error']}")

            chunk = self._extract_stream_chunk(payload)
            if chunk:
                yield chunk

    def _extract_stream_chunk(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""

        choices = payload.get("choices")
        if not isinstance(choices, list):
            return ""

        fragments: list[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue

            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue

            chunk = self._normalize_content(delta.get("content"))
            if chunk:
                fragments.append(chunk)

        return "".join(fragments)

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

    def _chunk_text(self, text: str, chunk_size: int = 12) -> Iterator[str]:
        normalized = text.strip()
        if not normalized:
            return

        for start in range(0, len(normalized), chunk_size):
            yield normalized[start : start + chunk_size]

    def _build_openai_payload(
        self,
        prompt: str,
        stream: bool,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "model": self._openai_model,
            "messages": self._build_openai_messages(prompt, metadata),
            "temperature": self._openai_temperature,
            "max_tokens": self._openai_max_tokens,
            "stream": stream,
        }

    def _build_openai_messages(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> list[dict[str, str]]:
        if metadata:
            raw_messages = metadata.get(MODEL_MESSAGES_METADATA_KEY, "").strip()
            if raw_messages:
                try:
                    parsed = json.loads(raw_messages)
                    if isinstance(parsed, list):
                        normalized: list[dict[str, str]] = []
                        for item in parsed:
                            if not isinstance(item, dict):
                                continue

                            role = item.get("role")
                            content = item.get("content")
                            if (
                                isinstance(role, str)
                                and role in {"system", "user", "assistant"}
                                and isinstance(content, str)
                                and content.strip()
                            ):
                                normalized.append(
                                    {
                                        "role": role,
                                        "content": content,
                                    }
                                )

                        if normalized:
                            return normalized
                except json.JSONDecodeError:
                    pass

        return [
            {
                "role": "system",
                "content": "You are Synapse runtime. Keep responses concise and practical.",
            },
            {"role": "user", "content": prompt},
        ]

    def _request_openai_completion(
        self, prompt: str, metadata: dict[str, str] | None = None
    ) -> str:
        endpoint = self._openai_base_url.strip() or "https://api.openai.com/v1"
        endpoint = endpoint.rstrip("/") + "/chat/completions"

        payload = self._build_openai_payload(prompt, stream=False, metadata=metadata)

        data = json.dumps(payload).encode("utf-8")
        response_payload = self._perform_request_with_retry(endpoint, data)

        choices = response_payload.get("choices") or []
        if not choices:
            return ""

        message = choices[0].get("message") or {}
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
            return content.strip()
        return ""

    def _perform_request_with_retry(self, endpoint: str, data: bytes) -> dict:
        retryable_http_status = {429, 500, 502, 503, 504}
        last_error: Exception | None = None

        for attempt in range(1, self._openai_max_retries + 1):
            request = urllib_request.Request(
                endpoint,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._openai_api_key}",
                },
                method="POST",
            )

            try:
                with urllib_request.urlopen(request, timeout=self._openai_http_timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib_error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="ignore")
                if exc.code not in retryable_http_status or attempt >= self._openai_max_retries:
                    raise RuntimeError(f"openai request failed: HTTP {exc.code} {body}") from exc

                retry_after_header = exc.headers.get("Retry-After") if exc.headers else None
                delay = self._compute_retry_delay(attempt, retry_after_header)
                time.sleep(delay)
                last_error = exc
            except urllib_error.URLError as exc:
                if attempt >= self._openai_max_retries:
                    raise RuntimeError(f"openai request failed: {exc.reason}") from exc

                delay = self._compute_retry_delay(attempt, None)
                time.sleep(delay)
                last_error = exc

        if last_error is not None:
            raise RuntimeError(f"openai request failed: {last_error}")
        raise RuntimeError("openai request failed: unknown error")

    def _compute_retry_delay(self, attempt: int, retry_after_header: str | None) -> float:
        if retry_after_header:
            try:
                parsed = float(retry_after_header)
                if parsed > 0:
                    return min(parsed, 20.0)
            except ValueError:
                pass

        # Linear backoff is enough here and keeps total wait bounded.
        return min(self._openai_retry_backoff_seconds * attempt, 10.0)

    def _build_response(self, prompt: str) -> str:
        # 统一 mock 响应格式，保证本地测试输出稳定。
        normalized_prompt = " ".join(prompt.strip().split())
        if not normalized_prompt:
            normalized_prompt = "empty request"

        return (
            "Synapse acknowledged your request: "
            f"{normalized_prompt}. "
            "Next milestone is replacing this mock runtime with real model routing."
        )
