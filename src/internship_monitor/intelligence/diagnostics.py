"""Read-only local-intelligence probes for explicit offline diagnostics."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from internship_monitor.analysis import DeterministicAssessor
from internship_monitor.config import SearchConfiguration
from internship_monitor.intelligence.agent import AgentError, OllamaAdjudicationClient
from internship_monitor.intelligence.embeddings import EmbeddingProviderError, OllamaEmbeddingClient
from internship_monitor.intelligence.failures import ProviderFailureCategory, failure_category
from internship_monitor.intelligence.providers import OllamaHealthProvider, ProviderHealth
from internship_monitor.intelligence.rag import CorpusError, LocalRagRetriever
from internship_monitor.intelligence.structured import (
    OllamaStructuredAssessmentClient,
    StructuredAssessmentError,
)
from internship_monitor.models import JobListing


@dataclass(frozen=True, slots=True)
class ProbeCheck:
    configured_model: str | None
    model_present: bool | None
    attempted: bool
    succeeded: bool
    latency_ms: float | None = None
    warm_latency_ms: float | None = None
    error_category: str | None = None
    vector_dimension: int | None = None
    finite_values: bool | None = None
    tool_names: tuple[str, ...] = ()
    retrieval_count: int = 0
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IntelligenceProbeReport:
    ollama: ProviderHealth
    embedding: ProbeCheck
    structured_llm: ProbeCheck
    agent: ProbeCheck
    rag_index_available: bool
    corpus_configured: bool


_SAFE_LISTING = JobListing.model_validate(
    {
        "source": "intelligence_probe",
        "source_job_id": "safe-ambiguous-internship",
        "company": "Example Company",
        "title": "Technical Solutions Internship",
        "description": (
            "This internship supports technical implementation, data analysis, customer workflows, "
            "and product delivery with engineering teams."
        ),
        "apply_url": "https://example.invalid/internship-probe",
        "location": "Remote",
        "employment_type": "Intern",
        "discovered_at": "2026-08-15T00:00:00Z",
    }
)
_EMBEDDING_PROBE_TEXT = "Technical software engineering internship with data and platform work."
T = TypeVar("T")


def probe_intelligence(
    configuration: SearchConfiguration,
    *,
    rag_index_path: Path,
    rag_corpus_dir: Path,
) -> IntelligenceProbeReport:
    """Run explicit direct probes without enabling monitor runtime or writing local caches."""
    health = OllamaHealthProvider(configuration.intelligence.ollama, enabled=True).health()
    installed = set(health.installed_models)
    embedding_model = configuration.intelligence.embedding.model
    llm_model = configuration.intelligence.structured_assessment.model
    embedding_present = embedding_model in installed if health.is_available else None
    llm_present = llm_model in installed if health.is_available else None
    if not health.is_available:
        unavailable = _unavailable_check(embedding_model)
        return IntelligenceProbeReport(
            health,
            unavailable,
            _unavailable_check(llm_model),
            _unavailable_check(configuration.intelligence.agent.model),
            rag_index_path.is_file(),
            rag_corpus_dir.is_dir(),
        )
    embedding = _embedding_probe(configuration, embedding_present)
    structured = _structured_probe(configuration, llm_present)
    agent = _agent_probe(
        configuration,
        model_present=llm_present,
        rag_index_path=rag_index_path,
    )
    return IntelligenceProbeReport(
        health,
        embedding,
        structured,
        agent,
        rag_index_path.is_file(),
        rag_corpus_dir.is_dir(),
    )


def _embedding_probe(configuration: SearchConfiguration, model_present: bool | None) -> ProbeCheck:
    model = configuration.intelligence.embedding.model
    if not model_present:
        return _missing_model_check(model)
    client = OllamaEmbeddingClient(configuration.intelligence.ollama, model)
    try:
        vector, cold = _measure(lambda: client.embed((_EMBEDDING_PROBE_TEXT,))[0])
        _warm_vector, warm = _measure(lambda: client.embed((_EMBEDDING_PROBE_TEXT,))[0])
    except (EmbeddingProviderError, OSError) as error:
        return ProbeCheck(model, True, True, False, error_category=failure_category(error).value)
    return ProbeCheck(
        model,
        True,
        True,
        True,
        latency_ms=cold,
        warm_latency_ms=warm,
        vector_dimension=len(vector),
        finite_values=True,
    )


def _structured_probe(configuration: SearchConfiguration, model_present: bool | None) -> ProbeCheck:
    model = configuration.intelligence.structured_assessment.model
    if not model_present:
        return _missing_model_check(model)
    client = OllamaStructuredAssessmentClient(configuration)
    try:
        _verdict, cold = _measure(lambda: client.assess(_SAFE_LISTING))
        _warm_verdict, warm = _measure(lambda: client.assess(_SAFE_LISTING))
    except (StructuredAssessmentError, OSError) as error:
        return ProbeCheck(model, True, True, False, error_category=failure_category(error).value)
    return ProbeCheck(model, True, True, True, latency_ms=cold, warm_latency_ms=warm)


def _agent_probe(
    configuration: SearchConfiguration,
    *,
    model_present: bool | None,
    rag_index_path: Path,
) -> ProbeCheck:
    model = configuration.intelligence.agent.model
    if not model_present:
        return _missing_model_check(model)
    if not rag_index_path.is_file():
        return ProbeCheck(
            model,
            True,
            False,
            False,
            error_category=ProviderFailureCategory.RETRIEVAL_UNAVAILABLE.value,
        )
    retriever = LocalRagRetriever(
        configuration=configuration,
        index_path=rag_index_path,
        embedding_cache_path=None,
    )
    assessment = DeterministicAssessor(configuration).assess(_SAFE_LISTING)
    client = OllamaAdjudicationClient(configuration)
    try:
        (_verdict, tools, contexts), latency = _measure(
            lambda: client.adjudicate(_SAFE_LISTING, assessment, retriever, force_retrieval=True)
        )
    except (AgentError, CorpusError, StructuredAssessmentError, OSError) as error:
        return ProbeCheck(model, True, True, False, error_category=failure_category(error).value)
    source_ids = tuple(context.document_id for context in contexts)
    succeeded = bool(tools) and bool(source_ids)
    return ProbeCheck(
        model,
        True,
        True,
        succeeded,
        latency_ms=latency,
        error_category=None if succeeded else ProviderFailureCategory.TOOL_PROTOCOL_FAILURE.value,
        tool_names=tools,
        retrieval_count=len(contexts),
        source_ids=source_ids,
    )


def _measure[T](call: Callable[[], T]) -> tuple[T, float]:
    started = time.perf_counter()
    value = call()
    return value, round((time.perf_counter() - started) * 1000, 3)


def _missing_model_check(model: str) -> ProbeCheck:
    return ProbeCheck(
        model,
        False,
        False,
        False,
        error_category=ProviderFailureCategory.MODEL_MISSING.value,
    )


def _unavailable_check(model: str) -> ProbeCheck:
    return ProbeCheck(
        model,
        None,
        False,
        False,
        error_category=ProviderFailureCategory.PROVIDER_UNREACHABLE.value,
    )


AgentErrorTypes = (AgentError, CorpusError, StructuredAssessmentError)
