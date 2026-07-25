import os
import unittest
from unittest.mock import Mock, patch

from app.config import load_config
from app.main import _build_memory_store, _build_model_provider, _model_provider_display_alias
from app.memory import FileMemoryStore
from app.providers import MockModelProvider, OpenAICompatibleProvider


class MemoryBackendFactoryTests(unittest.TestCase):
    def test_builds_file_memory_store_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()

        store = _build_memory_store(config)

        self.assertIsInstance(store, FileMemoryStore)

    def test_builds_vector_memory_store_when_configured(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SYNAPSE_MEMORY_BACKEND": "vector",
                "SYNAPSE_VECTOR_DATABASE_URL": "postgresql://synapse:synapse@postgres:5432/synapse",
                "SYNAPSE_VECTOR_EMBEDDING_PROVIDER": "openai_compatible",
                "SYNAPSE_VECTOR_EMBEDDING_MODEL": "text-embedding-3-small",
                "SYNAPSE_VECTOR_EMBEDDING_BASE_URL": "https://api.openai.com/v1",
                "SYNAPSE_VECTOR_EMBEDDING_API_KEY": "secret",
                "SYNAPSE_VECTOR_EMBEDDING_DIMENSION": "1536",
                "SYNAPSE_VECTOR_MEMORY_TOP_K": "24",
            },
            clear=True,
        ):
            config = load_config()

        sentinel_store = Mock()
        sentinel_provider = Mock()
        with patch(
            "app.main.OpenAICompatibleEmbeddingProvider",
            return_value=sentinel_provider,
        ) as provider_cls, patch(
            "app.main.PostgresVectorMemoryStore",
            return_value=sentinel_store,
        ) as store_cls:
            store = _build_memory_store(config)

        self.assertIs(store, sentinel_store)
        provider_cls.assert_called_once_with(
            model="text-embedding-3-small",
            base_url="https://api.openai.com/v1",
            api_key="secret",
        )
        store_cls.assert_called_once_with(
            database_url="postgresql://synapse:synapse@postgres:5432/synapse",
            embedding_provider=sentinel_provider,
            embedding_dimension=1536,
            top_k=24,
            max_entries_per_user=80,
        )

    def test_builds_mock_model_provider_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()

        provider = _build_model_provider(config)

        self.assertIsInstance(provider, MockModelProvider)
        self.assertEqual(_model_provider_display_alias(config), "")

    def test_builds_openai_compatible_provider_from_existing_env_names(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SYNAPSE_MODEL_PROVIDER": "openai",
                "SYNAPSE_OPENAI_API_KEY": "test-key",
                "SYNAPSE_OPENAI_BASE_URL": "https://provider.example/v1/",
                "SYNAPSE_OPENAI_MODEL": "test-model",
                "SYNAPSE_OPENAI_TEMPERATURE": "0.4",
                "SYNAPSE_OPENAI_MAX_TOKENS": "128",
                "SYNAPSE_OPENAI_HTTP_TIMEOUT_SECONDS": "9",
                "SYNAPSE_OPENAI_MAX_RETRIES": "2",
                "SYNAPSE_OPENAI_RETRY_BACKOFF_SECONDS": "0.5",
            },
            clear=True,
        ):
            config = load_config()

        provider = _build_model_provider(config)

        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(
            provider.endpoint,
            "https://provider.example/v1/chat/completions",
        )

    def test_gemini_and_zhipu_aliases_still_display_as_aliases(self) -> None:
        for alias in ("gemini", "zhipu"):
            with self.subTest(alias=alias):
                with patch.dict(
                    os.environ,
                    {
                        "SYNAPSE_MODEL_PROVIDER": alias,
                        "SYNAPSE_OPENAI_API_KEY": "test-key",
                    },
                    clear=True,
                ):
                    config = load_config()

                provider = _build_model_provider(config)

                self.assertIsInstance(provider, OpenAICompatibleProvider)
                self.assertEqual(_model_provider_display_alias(config), alias)


if __name__ == "__main__":
    unittest.main()
