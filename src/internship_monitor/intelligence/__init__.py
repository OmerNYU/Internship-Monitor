"""Optional local intelligence-provider integration boundary."""

from internship_monitor.intelligence.agent import (
    AgentError,
    AgenticAdjudicationProvider,
    AgentRoleVerdict,
    OllamaAdjudicationClient,
)
from internship_monitor.intelligence.diagnostics import (
    IntelligenceProbeReport,
    ProbeCheck,
    probe_intelligence,
)
from internship_monitor.intelligence.embeddings import (
    EmbeddingCache,
    EmbeddingProviderError,
    OllamaEmbeddingClient,
)
from internship_monitor.intelligence.failures import ProviderFailureCategory, failure_category
from internship_monitor.intelligence.providers import (
    IntelligenceProvider,
    OllamaHealthProvider,
    ProviderHealth,
    ProviderHealthStatus,
    provider_from_configuration,
)
from internship_monitor.intelligence.rag import (
    CorpusChunk,
    CorpusDocument,
    CorpusError,
    CorpusKind,
    LocalRagRetriever,
    RagRetriever,
    RetrievedContext,
    build_corpus_index,
    load_private_documents,
)
from internship_monitor.intelligence.semantic import (
    EmbeddingAssessmentProvider,
    RoleArchetype,
    cosine_similarity,
    role_archetypes,
)
from internship_monitor.intelligence.structured import (
    OllamaStructuredAssessmentClient,
    StructuredAssessmentError,
    StructuredLLMAssessmentProvider,
    StructuredRoleVerdict,
)

__all__ = [
    "AgentError",
    "AgentRoleVerdict",
    "AgenticAdjudicationProvider",
    "CorpusChunk",
    "CorpusDocument",
    "CorpusError",
    "CorpusKind",
    "EmbeddingAssessmentProvider",
    "EmbeddingCache",
    "EmbeddingProviderError",
    "IntelligenceProbeReport",
    "IntelligenceProvider",
    "LocalRagRetriever",
    "OllamaAdjudicationClient",
    "OllamaEmbeddingClient",
    "OllamaHealthProvider",
    "OllamaStructuredAssessmentClient",
    "ProbeCheck",
    "ProviderFailureCategory",
    "ProviderHealth",
    "ProviderHealthStatus",
    "RagRetriever",
    "RetrievedContext",
    "RoleArchetype",
    "StructuredAssessmentError",
    "StructuredLLMAssessmentProvider",
    "StructuredRoleVerdict",
    "build_corpus_index",
    "cosine_similarity",
    "failure_category",
    "load_private_documents",
    "probe_intelligence",
    "provider_from_configuration",
    "role_archetypes",
]
