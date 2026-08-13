"""Private local corpus indexing and deterministic retrieval for offline intelligence."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from internship_monitor.config import SearchConfiguration
from internship_monitor.evaluation import GoldDatasetError, load_gold_cases
from internship_monitor.intelligence.embeddings import (
    EmbeddingCache,
    EmbeddingProviderError,
    OllamaEmbeddingClient,
    cached_embeddings,
)
from internship_monitor.intelligence.semantic import cosine_similarity, role_archetypes


class CorpusError(RuntimeError):
    """Private corpus input or index state is invalid."""


class CorpusKind(StrEnum):
    PROFILE = "profile"
    PROJECT = "project"
    ROLE = "role"
    POLICY = "policy"
    LABELED_EXAMPLE = "labeled_example"


class _MarkdownFrontMatter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1, le=1)
    id: str = Field(min_length=1, max_length=100)
    kind: CorpusKind
    title: str = Field(min_length=1, max_length=200)
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    id: str
    kind: CorpusKind
    title: str
    text: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CorpusChunk:
    document_id: str
    kind: CorpusKind
    chunk_index: int
    text: str


@dataclass(frozen=True, slots=True)
class RetrievedContext:
    document_id: str
    kind: CorpusKind
    chunk_index: int
    excerpt: str
    similarity: float


class RagRetriever(Protocol):
    def retrieve(
        self, query: str, *, kinds: tuple[CorpusKind, ...] | None = None, limit: int = 4
    ) -> tuple[RetrievedContext, ...]: ...


def load_private_documents(corpus_dir: Path) -> tuple[CorpusDocument, ...]:
    """Load strict private Markdown documents without following unsafe paths."""
    root = corpus_dir.resolve()
    if not root.is_dir():
        raise CorpusError(f"corpus directory does not exist: {corpus_dir}")
    documents: list[CorpusDocument] = []
    for path in sorted(root.rglob("*.md")):
        resolved = path.resolve()
        if root not in resolved.parents:
            raise CorpusError(f"corpus file escapes corpus directory: {path}")
        documents.append(_parse_markdown(resolved))
    return _unique_documents(documents)


def generated_documents(configuration: SearchConfiguration) -> tuple[CorpusDocument, ...]:
    """Make role and policy retrieval context from already validated configuration."""
    roles = tuple(
        CorpusDocument(
            id=f"role:{archetype.category}",
            kind=CorpusKind.ROLE,
            title=f"Configured {archetype.category} roles",
            text=archetype.text,
        )
        for archetype in role_archetypes(configuration)
    )
    policy = CorpusDocument(
        id="policy:eligibility-routing",
        kind=CorpusKind.POLICY,
        title="Configured eligibility and geographic policy",
        text=(
            f"Primary regions: {', '.join(configuration.regional_strategy.primary_regions)}. "
            f"Preferred markets: {', '.join(market.country for market in configuration.regional_strategy.preferred_markets)}. "  # noqa: E501
            f"Hard excluded countries: {', '.join(configuration.regional_strategy.hard_excluded_countries) or 'none'}. "  # noqa: E501
            f"Supported authorization countries: {', '.join(configuration.authorization.supported_countries) or 'none'}. "  # noqa: E501
            f"Professional languages: {', '.join(configuration.language_profile.spoken_languages) or 'unknown'}."  # noqa: E501
        ),
    )
    return (*roles, policy)


def labeled_example_documents(path: Path | None) -> tuple[CorpusDocument, ...]:
    """Optionally derive retrieval-only labeled examples from a supplied private gold dataset."""
    if path is None:
        return ()
    try:
        cases = load_gold_cases(path)
    except GoldDatasetError as error:
        raise CorpusError(str(error)) from error
    return tuple(
        CorpusDocument(
            id=f"labeled:{case.case_id}",
            kind=CorpusKind.LABELED_EXAMPLE,
            title=case.listing.title,
            text=(
                f"Title: {case.listing.title}\nDescription: {case.listing.description}\n"
                f"Human role label: {case.expected.role_level.value}. "
                f"Human actionability: {case.expected.actionability.value}."
            ),
        )
        for case in cases
    )


def build_corpus_index(
    *,
    configuration: SearchConfiguration,
    corpus_dir: Path,
    index_path: Path,
    embedding_cache_path: Path,
    labeled_dataset: Path | None = None,
    client: OllamaEmbeddingClient | None = None,
) -> int:
    """Validate, embed, and atomically replace one local corpus index."""
    documents = _unique_documents(
        (
            *load_private_documents(corpus_dir),
            *generated_documents(configuration),
            *labeled_example_documents(labeled_dataset),
        )
    )
    chunks = tuple(chunk for document in documents for chunk in _chunks(document))
    model = configuration.intelligence.embedding.model
    embedder = client or OllamaEmbeddingClient(configuration.intelligence.ollama, model)
    try:
        with EmbeddingCache(embedding_cache_path) as cache:
            cached_embeddings(
                model=model,
                texts=tuple(chunk.text for chunk in chunks),
                cache=cache,
                embed=embedder.embed,
            )
    except (EmbeddingProviderError, OSError, sqlite3.Error) as error:
        raise CorpusError(f"could not embed corpus: {error}") from error
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(index_path) as connection:
        connection.execute("BEGIN")
        connection.execute("DROP TABLE IF EXISTS corpus_chunks")
        connection.execute(
            """
            CREATE TABLE corpus_chunks (
                document_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                PRIMARY KEY (document_id, chunk_index)
            )
            """
        )
        connection.executemany(
            "INSERT INTO corpus_chunks VALUES (?, ?, ?, ?, ?)",
            (
                (
                    chunk.document_id,
                    chunk.kind.value,
                    chunk.chunk_index,
                    chunk.text,
                    _fingerprint(chunk.text),
                )
                for chunk in chunks
            ),
        )
    return len(chunks)


class LocalRagRetriever:
    """Read a local corpus index and return deterministic cosine-ranked excerpts."""

    def __init__(
        self,
        *,
        configuration: SearchConfiguration,
        index_path: Path,
        embedding_cache_path: Path,
        client: OllamaEmbeddingClient | None = None,
    ) -> None:
        self._configuration = configuration
        self._index_path = index_path
        self._cache_path = embedding_cache_path
        self._client = client or OllamaEmbeddingClient(
            configuration.intelligence.ollama, configuration.intelligence.embedding.model
        )

    def retrieve(
        self, query: str, *, kinds: tuple[CorpusKind, ...] | None = None, limit: int = 4
    ) -> tuple[RetrievedContext, ...]:
        if not query.strip() or not 1 <= limit <= 8:
            raise CorpusError("query must be non-empty and limit must be between 1 and 8")
        if not self._index_path.is_file():
            raise CorpusError(f"corpus index does not exist: {self._index_path}")
        with sqlite3.connect(self._index_path) as connection:
            rows = connection.execute(
                "SELECT document_id, kind, chunk_index, text FROM corpus_chunks"
            ).fetchall()
        chunks = tuple(
            CorpusChunk(row[0], CorpusKind(row[1]), row[2], row[3])
            for row in rows
            if kinds is None or CorpusKind(row[1]) in kinds
        )
        model = self._configuration.intelligence.embedding.model
        try:
            with EmbeddingCache(self._cache_path) as cache:
                vectors = cached_embeddings(
                    model=model,
                    texts=(query, *(chunk.text for chunk in chunks)),
                    cache=cache,
                    embed=self._client.embed,
                )
        except (EmbeddingProviderError, OSError, sqlite3.Error) as error:
            raise CorpusError(f"could not retrieve corpus context: {error}") from error
        query_vector, *chunk_vectors = vectors
        results = tuple(
            RetrievedContext(
                chunk.document_id,
                chunk.kind,
                chunk.chunk_index,
                chunk.text[:600],
                round(cosine_similarity(query_vector, vector), 6),
            )
            for chunk, vector in zip(chunks, chunk_vectors, strict=True)
        )
        return tuple(
            sorted(
                results,
                key=lambda item: (-item.similarity, item.document_id, item.chunk_index),
            )[:limit]
        )


def _parse_markdown(path: Path) -> CorpusDocument:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise CorpusError(f"corpus document requires YAML front matter: {path}")
    _, separator, _remaining = text[4:].partition("\n---\n")
    if not separator:
        raise CorpusError(f"corpus document front matter is not closed: {path}")
    front_matter, body = text[4:].split("\n---\n", maxsplit=1)
    try:
        metadata = _MarkdownFrontMatter.model_validate(yaml.safe_load(front_matter))
    except (ValidationError, yaml.YAMLError) as error:
        raise CorpusError(f"invalid corpus front matter in {path}: {error}") from error
    if metadata.kind not in {CorpusKind.PROFILE, CorpusKind.PROJECT} or not body.strip():
        raise CorpusError(f"private corpus document has invalid kind or empty body: {path}")
    return CorpusDocument(metadata.id, metadata.kind, metadata.title, body.strip(), metadata.tags)


def _chunks(document: CorpusDocument, size: int = 1200) -> tuple[CorpusChunk, ...]:
    parts = tuple(
        document.text[index : index + size].strip() for index in range(0, len(document.text), size)
    )
    return tuple(
        CorpusChunk(document.id, document.kind, index, part)
        for index, part in enumerate(parts)
        if part
    )


def _unique_documents(
    documents: list[CorpusDocument] | tuple[CorpusDocument, ...],
) -> tuple[CorpusDocument, ...]:
    ids = [document.id for document in documents]
    if len(set(ids)) != len(ids):
        raise CorpusError("corpus document IDs must be unique")
    return tuple(documents)


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
