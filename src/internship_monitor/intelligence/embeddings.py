"""Validated local Ollama embedding access and a content-addressed SQLite cache."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx

from internship_monitor.config import OllamaConfiguration


class EmbeddingProviderError(RuntimeError):
    """A local embedding request or response could not be used safely."""


class EmbeddingCache:
    """Small local cache keyed by model and normalized source text."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS embedding_cache (
                model TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                PRIMARY KEY (model, text_hash)
            )
            """
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> EmbeddingCache:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get(self, model: str, text: str) -> tuple[float, ...] | None:
        row = self._connection.execute(
            "SELECT vector_json FROM embedding_cache WHERE model = ? AND text_hash = ?",
            (model, _text_hash(text)),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row[0])
        except (TypeError, json.JSONDecodeError) as error:
            raise EmbeddingProviderError("cached embedding is not valid JSON") from error
        return _validated_vector(payload)

    def put(self, model: str, text: str, vector: tuple[float, ...]) -> None:
        self._connection.execute(
            (
                "INSERT OR REPLACE INTO embedding_cache "
                "(model, text_hash, vector_json) VALUES (?, ?, ?)"
            ),
            (model, _text_hash(text), json.dumps(vector, separators=(",", ":"))),
        )
        self._connection.commit()


class OllamaEmbeddingClient:
    """Synchronous, local-only client for Ollama's documented /api/embed endpoint."""

    def __init__(
        self,
        configuration: OllamaConfiguration,
        model: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._configuration = configuration
        self._model = model
        self._transport = transport

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Validate model availability, then embed the supplied texts as one batch."""
        if not texts:
            return ()
        try:
            with httpx.Client(
                base_url=self._configuration.base_url,
                timeout=self._configuration.inference_timeout_seconds,
                transport=self._transport,
            ) as client:
                self._require_model(client)
                response = client.post(
                    "/api/embed",
                    json={"model": self._model, "input": list(texts), "truncate": True},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise EmbeddingProviderError("local Ollama embedding request failed") from error
        vectors = _vectors_from_payload(payload)
        if len(vectors) != len(texts):
            raise EmbeddingProviderError("Ollama returned a different number of embedding vectors")
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1:
            raise EmbeddingProviderError(
                "Ollama returned embedding vectors with different dimensions"
            )
        return vectors

    def _require_model(self, client: httpx.Client) -> None:
        response = client.get("/api/tags")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise EmbeddingProviderError("Ollama model-list response is invalid")
        names = {
            model.get("name", "").strip()
            for model in payload["models"]
            if isinstance(model, dict) and isinstance(model.get("name"), str)
        }
        if self._model not in names:
            raise EmbeddingProviderError(
                f"configured embedding model is not installed: {self._model}"
            )


def cached_embeddings(
    *,
    model: str,
    texts: Sequence[str],
    cache: EmbeddingCache | None,
    embed: Callable[[Sequence[str]], tuple[tuple[float, ...], ...]],
) -> tuple[tuple[float, ...], ...]:
    """Read cached vectors when possible and embed/cache the remaining texts in order."""
    vectors: list[tuple[float, ...] | None] = []
    missing: list[str] = []
    missing_positions: list[int] = []
    for position, text in enumerate(texts):
        cached = cache.get(model, text) if cache is not None else None
        vectors.append(cached)
        if cached is None:
            missing.append(text)
            missing_positions.append(position)
    if missing:
        created = embed(missing)
        if len(created) != len(missing):
            raise EmbeddingProviderError("embedding callback returned an unexpected vector count")
        for position, text, vector in zip(missing_positions, missing, created, strict=True):
            if cache is not None:
                cache.put(model, text, vector)
            vectors[position] = vector
    if any(vector is None for vector in vectors):
        raise EmbeddingProviderError("embedding cache did not resolve every requested vector")
    return tuple(vector for vector in vectors if vector is not None)


def _text_hash(text: str) -> str:
    normalized = " ".join(text.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _vectors_from_payload(payload: object) -> tuple[tuple[float, ...], ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("embeddings"), list):
        raise EmbeddingProviderError("Ollama embedding response must contain an embeddings list")
    return tuple(_validated_vector(vector) for vector in payload["embeddings"])


def _validated_vector(value: object) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise EmbeddingProviderError("embedding vectors must be non-empty arrays")
    if any(isinstance(item, bool) or not isinstance(item, int | float) for item in value):
        raise EmbeddingProviderError("embedding vectors must contain only numeric values")
    vector = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in vector):
        raise EmbeddingProviderError("embedding vectors must contain only finite values")
    return vector
