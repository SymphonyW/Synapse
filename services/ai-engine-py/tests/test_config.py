import os
import unittest
from unittest.mock import patch

from app.config import load_config


class ConfigTests(unittest.TestCase):
    def test_vector_memory_defaults_to_file_backend(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()

        self.assertEqual(config.memory_backend, "file")
        self.assertEqual(config.vector_database_url, "")
        self.assertEqual(config.vector_embedding_provider, "")
        self.assertEqual(config.vector_embedding_model, "")
        self.assertEqual(config.vector_embedding_base_url, "")
        self.assertEqual(config.vector_embedding_api_key, "")
        self.assertEqual(config.vector_embedding_dimension, 0)
        self.assertEqual(config.vector_memory_top_k, 10)

    def test_vector_memory_backend_requires_complete_configuration(self) -> None:
        with patch.dict(os.environ, {"SYNAPSE_MEMORY_BACKEND": "vector"}, clear=True):
            with self.assertRaisesRegex(ValueError, "SYNAPSE_VECTOR_DATABASE_URL"):
                load_config()

    def test_vector_memory_backend_loads_pgvector_settings(self) -> None:
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

        self.assertEqual(config.memory_backend, "vector")
        self.assertEqual(config.vector_database_url, "postgresql://synapse:synapse@postgres:5432/synapse")
        self.assertEqual(config.vector_embedding_provider, "openai_compatible")
        self.assertEqual(config.vector_embedding_model, "text-embedding-3-small")
        self.assertEqual(config.vector_embedding_base_url, "https://api.openai.com/v1")
        self.assertEqual(config.vector_embedding_api_key, "secret")
        self.assertEqual(config.vector_embedding_dimension, 1536)
        self.assertEqual(config.vector_memory_top_k, 24)

    def test_loads_openai_continuation_and_agent_timeout_settings(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SYNAPSE_OPENAI_CONTINUATION_MAX_ROUNDS": "3",
                "SYNAPSE_OPENAI_LONG_FORM_MIN_CHARS": "3200",
                "SYNAPSE_AGENT_GENERATION_TIMEOUT_SECONDS": "180",
                "SYNAPSE_AGENT_STREAM_IDLE_TIMEOUT_SECONDS": "45",
            },
            clear=True,
        ):
            config = load_config()

        self.assertEqual(config.openai_continuation_max_rounds, 3)
        self.assertEqual(config.openai_long_form_min_chars, 3200)
        self.assertEqual(config.agent_generation_timeout_seconds, 180)
        self.assertEqual(config.agent_stream_idle_timeout_seconds, 45)

    def test_mcp_stdio_defaults_to_disabled_without_transport_config(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()

        self.assertFalse(config.mcp_stdio_enabled)
        self.assertEqual(config.mcp_stdio_command, "")
        self.assertEqual(config.mcp_stdio_args, ())
        self.assertEqual(config.mcp_stdio_env, {})
        self.assertEqual(config.mcp_stdio_workdir, "")
        self.assertEqual(config.mcp_stdio_timeout_seconds, 10.0)
        self.assertEqual(config.mcp_tool_name_prefix, "mcp")

    def test_mcp_stdio_enabled_requires_command(self) -> None:
        with patch.dict(os.environ, {"SYNAPSE_MCP_STDIO_ENABLED": "true"}, clear=True):
            with self.assertRaisesRegex(ValueError, "SYNAPSE_MCP_STDIO_COMMAND"):
                load_config()

    def test_mcp_stdio_parses_json_args_and_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SYNAPSE_MCP_STDIO_ENABLED": "true",
                "SYNAPSE_MCP_STDIO_COMMAND": "python",
                "SYNAPSE_MCP_STDIO_ARGS_JSON": '["-m", "fake_server"]',
                "SYNAPSE_MCP_STDIO_ENV_JSON": '{"FAKE_MODE": "normal", "COUNT": 3}',
                "SYNAPSE_MCP_STDIO_WORKDIR": "/tmp/mcp",
                "SYNAPSE_MCP_STDIO_TIMEOUT_SECONDS": "2.5",
                "SYNAPSE_MCP_TOOL_NAME_PREFIX": "remote",
            },
            clear=True,
        ):
            config = load_config()

        self.assertTrue(config.mcp_stdio_enabled)
        self.assertEqual(config.mcp_stdio_command, "python")
        self.assertEqual(config.mcp_stdio_args, ("-m", "fake_server"))
        self.assertEqual(config.mcp_stdio_env, {"FAKE_MODE": "normal", "COUNT": "3"})
        self.assertEqual(config.mcp_stdio_workdir, "/tmp/mcp")
        self.assertEqual(config.mcp_stdio_timeout_seconds, 2.5)
        self.assertEqual(config.mcp_tool_name_prefix, "remote")

    def test_mcp_stdio_rejects_malformed_json_config(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SYNAPSE_MCP_STDIO_ENABLED": "true",
                "SYNAPSE_MCP_STDIO_COMMAND": "python",
                "SYNAPSE_MCP_STDIO_ARGS_JSON": '{"bad": true}',
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "SYNAPSE_MCP_STDIO_ARGS_JSON"):
                load_config()

    def test_openapi_defaults_to_disabled_without_spec_config(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()

        self.assertFalse(config.openapi_enabled)
        self.assertEqual(config.openapi_spec_file, "")
        self.assertEqual(config.openapi_base_url_override, "")
        self.assertEqual(config.openapi_static_headers, {})
        self.assertEqual(config.openapi_bearer_token, "")
        self.assertEqual(config.openapi_api_key_header, "")
        self.assertEqual(config.openapi_api_key_value, "")
        self.assertEqual(config.openapi_http_timeout_seconds, 12.0)
        self.assertEqual(config.openapi_max_response_bytes, 65536)
        self.assertEqual(config.openapi_allowed_schemes, ("http", "https"))

    def test_openapi_enabled_requires_spec_file(self) -> None:
        with patch.dict(os.environ, {"SYNAPSE_OPENAPI_ENABLED": "true"}, clear=True):
            with self.assertRaisesRegex(ValueError, "SYNAPSE_OPENAPI_SPEC_FILE"):
                load_config()

    def test_openapi_parses_executor_config(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SYNAPSE_OPENAPI_ENABLED": "true",
                "SYNAPSE_OPENAPI_SPEC_FILE": "openapi.json",
                "SYNAPSE_OPENAPI_BASE_URL_OVERRIDE": "https://api.example.com/v1",
                "SYNAPSE_OPENAPI_STATIC_HEADERS_JSON": '{"X-Client": "synapse"}',
                "SYNAPSE_OPENAPI_BEARER_TOKEN": "bearer-secret",
                "SYNAPSE_OPENAPI_API_KEY_HEADER": "X-API-Key",
                "SYNAPSE_OPENAPI_API_KEY_VALUE": "key-secret",
                "SYNAPSE_OPENAPI_HTTP_TIMEOUT_SECONDS": "3.5",
                "SYNAPSE_OPENAPI_MAX_RESPONSE_BYTES": "4096",
                "SYNAPSE_OPENAPI_ALLOWED_SCHEMES": "https",
            },
            clear=True,
        ):
            config = load_config()

        self.assertTrue(config.openapi_enabled)
        self.assertEqual(config.openapi_spec_file, "openapi.json")
        self.assertEqual(config.openapi_base_url_override, "https://api.example.com/v1")
        self.assertEqual(config.openapi_static_headers, {"X-Client": "synapse"})
        self.assertEqual(config.openapi_bearer_token, "bearer-secret")
        self.assertEqual(config.openapi_api_key_header, "X-API-Key")
        self.assertEqual(config.openapi_api_key_value, "key-secret")
        self.assertEqual(config.openapi_http_timeout_seconds, 3.5)
        self.assertEqual(config.openapi_max_response_bytes, 4096)
        self.assertEqual(config.openapi_allowed_schemes, ("https",))

    def test_openapi_rejects_malformed_static_headers(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SYNAPSE_OPENAPI_ENABLED": "true",
                "SYNAPSE_OPENAPI_SPEC_FILE": "openapi.json",
                "SYNAPSE_OPENAPI_STATIC_HEADERS_JSON": "[1, 2]",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "SYNAPSE_OPENAPI_STATIC_HEADERS_JSON"):
                load_config()


if __name__ == "__main__":
    unittest.main()
