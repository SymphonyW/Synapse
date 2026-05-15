from __future__ import annotations

from typing import Mapping, Protocol, Sequence


class EmbeddingProviderError(RuntimeError):
    """Raised when an embedding provider cannot produce a usable vector."""


class EmbeddingProvider(Protocol):
    def embed_text(self, text: str) -> list[float]:
        ...


class MockEmbeddingProvider:
    """Deterministic test helper backed by explicit text -> vector mappings."""

    def __init__(self, vectors_by_text: Mapping[str, Sequence[float]]) -> None:
        self.vectors_by_text = {
            str(text): [float(value) for value in vector]
            for text, vector in vectors_by_text.items()
        }

    def embed_text(self, text: str) -> list[float]:
        if text not in self.vectors_by_text:
            raise EmbeddingProviderError(f"no mock embedding configured for text: {text}")
        return list(self.vectors_by_text[text])
