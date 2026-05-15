import json
import unittest
from unittest.mock import patch

from app.embeddings.base import EmbeddingProviderError, MockEmbeddingProvider
from app.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class EmbeddingProviderTests(unittest.TestCase):
    def test_mock_embedding_provider_returns_configured_vector(self) -> None:
        provider = MockEmbeddingProvider({"gateway retries": [1.0, 0.0, 0.0]})

        self.assertEqual(provider.embed_text("gateway retries"), [1.0, 0.0, 0.0])

    def test_mock_embedding_provider_raises_for_unknown_text(self) -> None:
        provider = MockEmbeddingProvider({})

        with self.assertRaisesRegex(EmbeddingProviderError, "no mock embedding"):
            provider.embed_text("missing")

    def test_openai_compatible_provider_reads_embeddings_response(self) -> None:
        provider = OpenAICompatibleEmbeddingProvider(
            model="text-embedding-demo",
            base_url="https://embeddings.example.com/v1/",
            api_key="secret",
        )

        with patch(
            "app.embeddings.openai_compatible.urllib_request.urlopen",
            return_value=_FakeResponse({"data": [{"embedding": [0.1, 0.2, 0.3]}]}),
        ) as urlopen:
            result = provider.embed_text("semantic memory")

        self.assertEqual(result, [0.1, 0.2, 0.3])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://embeddings.example.com/v1/embeddings")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")

    def test_openai_compatible_provider_rejects_malformed_response(self) -> None:
        provider = OpenAICompatibleEmbeddingProvider(
            model="text-embedding-demo",
            base_url="https://embeddings.example.com/v1",
            api_key="secret",
        )

        with patch(
            "app.embeddings.openai_compatible.urllib_request.urlopen",
            return_value=_FakeResponse({"data": [{}]}),
        ):
            with self.assertRaisesRegex(EmbeddingProviderError, "embedding"):
                provider.embed_text("semantic memory")


if __name__ == "__main__":
    unittest.main()
