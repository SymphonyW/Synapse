import asyncio
import json
import time
from typing import AsyncIterator
from urllib import error as urllib_error
from urllib import request as urllib_request


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

    async def run_prompt(self, prompt: str) -> AsyncIterator[str]:
        # provider 分流集中在此，保持 service 层与具体模型解耦。
        if self.model_provider == "openai":
            async for token in self._run_openai(prompt):
                yield token
            return

        async for token in self._run_mock(prompt):
            yield token

    async def _run_mock(self, prompt: str) -> AsyncIterator[str]:
        # Mock 模式按词流式输出，便于联调与集成测试。
        response = self._build_response(prompt)
        for token in response.split():
            await asyncio.sleep(0.06)
            yield token

    async def _run_openai(self, prompt: str) -> AsyncIterator[str]:
        normalized_prompt = " ".join(prompt.strip().split())
        if not normalized_prompt:
            normalized_prompt = "empty request"

        response_text = await asyncio.to_thread(self._request_openai_completion, normalized_prompt)
        if not response_text:
            yield "(empty response)"
            return

        for token in response_text.split():
            yield token

    def _request_openai_completion(self, prompt: str) -> str:
        endpoint = self._openai_base_url.strip() or "https://api.openai.com/v1"
        endpoint = endpoint.rstrip("/") + "/chat/completions"

        payload = {
            "model": self._openai_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are Synapse runtime. Keep responses concise and practical.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self._openai_temperature,
            "max_tokens": self._openai_max_tokens,
        }

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
