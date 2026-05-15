from __future__ import annotations

import json
from urllib import error as urllib_error
from urllib import request as urllib_request

from app.embeddings.base import EmbeddingProviderError


class OpenAICompatibleEmbeddingProvider:
    """Embedding provider for OpenAI-compatible `/embeddings` endpoints."""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._model = model.strip()
        self._base_url = base_url.strip() or "https://api.openai.com/v1"
        self._api_key = api_key.strip()
        self._timeout_seconds = max(1.0, timeout_seconds)

        if not self._model:
            raise ValueError("embedding model is required")
        if not self._api_key:
            raise ValueError("embedding api key is required")

    def embed_text(self, text: str) -> list[float]:
        normalized = text.strip()
        if not normalized:
            raise EmbeddingProviderError("embedding input text must not be empty")

        endpoint = self._base_url.rstrip("/") + "/embeddings"
        payload = json.dumps({"model": self._model, "input": normalized}).encode("utf-8")
        request = urllib_request.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        try:
            with urllib_request.urlopen(request, timeout=self._timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise EmbeddingProviderError(
                f"embedding request failed: HTTP {exc.code} {body}"
            ) from exc
        except urllib_error.URLError as exc:
            raise EmbeddingProviderError(f"embedding request failed: {exc.reason}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise EmbeddingProviderError("embedding response was not valid JSON") from exc

        data = decoded.get("data") if isinstance(decoded, dict) else None
        first = data[0] if isinstance(data, list) and data else None
        embedding = first.get("embedding") if isinstance(first, dict) else None
        if not isinstance(embedding, list) or not embedding:
            raise EmbeddingProviderError("embedding response did not contain an embedding vector")

        vector: list[float] = []
        try:
            vector = [float(value) for value in embedding]
        except (TypeError, ValueError) as exc:
            raise EmbeddingProviderError("embedding vector contained non-numeric values") from exc

        return vector
