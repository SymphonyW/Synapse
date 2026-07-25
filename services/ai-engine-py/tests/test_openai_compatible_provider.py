import asyncio
import io
import json
import unittest
from unittest.mock import patch
from urllib import error as urllib_error

from app.providers import (
    ModelProviderError,
    ModelStreamItem,
    OpenAICompatibleProvider,
)


class OpenAICompatibleProviderTests(unittest.TestCase):
    def test_stream_endpoint_headers_and_payload(self) -> None:
        calls: list[tuple[object, float]] = []
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="https://provider.example/v1/",
            model="test-model",
            temperature=0.7,
            max_tokens=123,
        )

        def fake_urlopen(request: object, timeout: float) -> _StreamResponse:
            calls.append((request, timeout))
            return _StreamResponse(
                [
                    b'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n',
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
                    b"data: [DONE]\n",
                ]
            )

        with patch("app.providers.openai_compatible.urllib_request.urlopen", fake_urlopen):
            items = list(provider.stream_sync([{"role": "user", "content": "hello"}]))

        self.assertEqual(provider.endpoint, "https://provider.example/v1/chat/completions")
        self.assertEqual(items, [ModelStreamItem(content="hello"), ModelStreamItem(finish_reason="stop")])
        request, timeout = calls[0]
        headers = _headers(request)
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, provider.endpoint)
        self.assertEqual(timeout, 45.0)
        self.assertEqual(headers["authorization"], "Bearer test-key")
        self.assertEqual(headers["accept"], "text/event-stream")
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["temperature"], 0.7)
        self.assertEqual(payload["max_tokens"], 123)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["messages"], [{"role": "user", "content": "hello"}])

    def test_completion_payload_and_finish_reason(self) -> None:
        calls: list[tuple[object, float]] = []
        provider = OpenAICompatibleProvider(api_key="test-key", base_url="")

        def fake_urlopen(request: object, timeout: float) -> _JSONResponse:
            calls.append((request, timeout))
            return _JSONResponse(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": " done "},
                        }
                    ]
                }
            )

        with patch("app.providers.openai_compatible.urllib_request.urlopen", fake_urlopen):
            result = provider.complete_sync([{"role": "user", "content": "finish"}])

        self.assertEqual(provider.endpoint, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(result.content, "done")
        self.assertEqual(result.finish_reason, "stop")
        request, _ = calls[0]
        headers = _headers(request)
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(headers["authorization"], "Bearer test-key")
        self.assertFalse(payload["stream"])

    def test_async_stream_uses_same_transport_contract(self) -> None:
        provider = OpenAICompatibleProvider(api_key="test-key")

        def fake_urlopen(request: object, timeout: float) -> _StreamResponse:
            _ = (request, timeout)
            return _StreamResponse(
                [
                    b'data: {"choices":[{"delta":{"content":"async"}}]}\n',
                    b"data: [DONE]\n",
                ]
            )

        with patch("app.providers.openai_compatible.urllib_request.urlopen", fake_urlopen):
            items = asyncio.run(_collect_stream(provider, [{"role": "user", "content": "hi"}]))

        self.assertEqual(items, [ModelStreamItem(content="async")])

    def test_sse_done_and_invalid_lines_are_ignored(self) -> None:
        provider = OpenAICompatibleProvider(api_key="test-key")

        def fake_urlopen(request: object, timeout: float) -> _StreamResponse:
            _ = (request, timeout)
            return _StreamResponse(
                [
                    b": keepalive\n",
                    b"event: message\n",
                    b"data: {not-json}\n",
                    b'data: {"choices":[{"delta":{"content":"valid"}}]}\n',
                    b"data: [DONE]\n",
                    b'data: {"choices":[{"delta":{"content":"ignored"}}]}\n',
                ]
            )

        with patch("app.providers.openai_compatible.urllib_request.urlopen", fake_urlopen):
            items = list(provider.stream_sync([{"role": "user", "content": "hi"}]))

        self.assertEqual(items, [ModelStreamItem(content="valid")])

    def test_content_normalization_accepts_strings_and_arrays(self) -> None:
        provider = OpenAICompatibleProvider(api_key="test-key")

        def fake_stream_urlopen(request: object, timeout: float) -> _StreamResponse:
            _ = (request, timeout)
            return _StreamResponse(
                [
                    b'data: {"choices":[{"delta":{"content":["a",{"text":"b"},{"content":"c"}]}}]}\n',
                    b"data: [DONE]\n",
                ]
            )

        with patch("app.providers.openai_compatible.urllib_request.urlopen", fake_stream_urlopen):
            stream_items = list(provider.stream_sync([{"role": "user", "content": "hi"}]))
        self.assertEqual(stream_items, [ModelStreamItem(content="abc")])

        def fake_completion_urlopen(request: object, timeout: float) -> _JSONResponse:
            _ = (request, timeout)
            return _JSONResponse(
                {
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {
                                "content": [
                                    {"type": "text", "text": "one"},
                                    {"type": "text", "text": "two"},
                                ]
                            },
                        }
                    ]
                }
            )

        with patch("app.providers.openai_compatible.urllib_request.urlopen", fake_completion_urlopen):
            completion = provider.complete_sync([{"role": "user", "content": "hi"}])
        self.assertEqual(completion.content, "one\ntwo")
        self.assertEqual(completion.finish_reason, "length")

    def test_http_429_and_500_are_retried(self) -> None:
        for status in (429, 500):
            with self.subTest(status=status):
                provider = OpenAICompatibleProvider(
                    api_key="test-key",
                    max_retries=2,
                    retry_backoff_seconds=0.2,
                )
                calls: list[object] = []

                def fake_urlopen(request: object, timeout: float) -> _JSONResponse:
                    _ = timeout
                    calls.append(request)
                    if len(calls) == 1:
                        raise urllib_error.HTTPError(
                            url="https://example.invalid/chat/completions",
                            code=status,
                            msg="controlled retry",
                            hdrs={"Retry-After": "0.3"},
                            fp=io.BytesIO(b"retry body"),
                        )
                    return _JSONResponse({"choices": [{"message": {"content": "ok"}}]})

                with patch("app.providers.openai_compatible.urllib_request.urlopen", fake_urlopen), patch(
                    "app.providers.openai_compatible.time.sleep"
                ) as sleep_mock:
                    result = provider.complete_sync([{"role": "user", "content": "hi"}])

                self.assertEqual(result.content, "ok")
                self.assertEqual(len(calls), 2)
                sleep_mock.assert_called_once_with(0.3)

    def test_non_retry_http_error_is_structured(self) -> None:
        provider = OpenAICompatibleProvider(api_key="test-key", max_retries=3)
        calls: list[object] = []

        def fake_urlopen(request: object, timeout: float) -> _JSONResponse:
            _ = timeout
            calls.append(request)
            raise urllib_error.HTTPError(
                url="https://example.invalid/chat/completions",
                code=400,
                msg="bad request",
                hdrs={},
                fp=io.BytesIO(b"bad body"),
            )

        with patch("app.providers.openai_compatible.urllib_request.urlopen", fake_urlopen):
            with self.assertRaises(ModelProviderError) as raised:
                provider.complete_sync([{"role": "user", "content": "hi"}])

        self.assertEqual(len(calls), 1)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(str(raised.exception), "openai request failed: HTTP 400 bad body")

    def test_network_exception_is_structured(self) -> None:
        provider = OpenAICompatibleProvider(api_key="test-key", max_retries=1)

        def fake_urlopen(request: object, timeout: float) -> _JSONResponse:
            _ = (request, timeout)
            raise urllib_error.URLError("network down")

        with patch("app.providers.openai_compatible.urllib_request.urlopen", fake_urlopen):
            with self.assertRaises(ModelProviderError) as raised:
                provider.complete_sync([{"role": "user", "content": "hi"}])

        self.assertEqual(raised.exception.code, "network_error")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(str(raised.exception), "openai request failed: network down")

    def test_empty_choices_returns_empty_completion_result(self) -> None:
        provider = OpenAICompatibleProvider(api_key="test-key")

        def fake_urlopen(request: object, timeout: float) -> _JSONResponse:
            _ = (request, timeout)
            return _JSONResponse({"choices": []})

        with patch("app.providers.openai_compatible.urllib_request.urlopen", fake_urlopen):
            result = provider.complete_sync([{"role": "user", "content": "hi"}])

        self.assertEqual(result.content, "")
        self.assertEqual(result.finish_reason, "")

    def test_stream_error_after_token_does_not_retry_whole_round(self) -> None:
        provider = OpenAICompatibleProvider(api_key="test-key", max_retries=3)
        calls: list[object] = []

        def fake_urlopen(request: object, timeout: float) -> _RaisingStreamResponse:
            _ = timeout
            calls.append(request)
            return _RaisingStreamResponse(
                [
                    b'data: {"choices":[{"delta":{"content":"first token"}}]}\n',
                ],
                urllib_error.URLError("stream closed"),
            )

        with patch("app.providers.openai_compatible.urllib_request.urlopen", fake_urlopen):
            iterator = provider.stream_sync([{"role": "user", "content": "hi"}])
            self.assertEqual(next(iterator), ModelStreamItem(content="first token"))
            with self.assertRaises(ModelProviderError) as raised:
                next(iterator)

        self.assertEqual(len(calls), 1)
        self.assertEqual(str(raised.exception), "openai stream request failed: stream closed")


class _StreamResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __enter__(self) -> "_StreamResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _RaisingStreamResponse(_StreamResponse):
    def __init__(self, lines: list[bytes], exc: Exception) -> None:
        super().__init__(lines)
        self._exc = exc

    def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        raise self._exc


class _JSONResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> "_JSONResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


async def _collect_stream(
    provider: OpenAICompatibleProvider,
    messages: list[dict[str, str]],
) -> list[ModelStreamItem]:
    items: list[ModelStreamItem] = []
    async for item in provider.stream(messages):
        items.append(item)
    return items


def _headers(request: object) -> dict[str, str]:
    return {
        str(key).lower(): str(value)
        for key, value in request.header_items()
    }


if __name__ == "__main__":
    unittest.main()
