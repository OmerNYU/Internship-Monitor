"""Optional local intelligence-provider integration boundary."""

from internship_monitor.intelligence.embeddings import (
    EmbeddingCache,
    EmbeddingProviderError,
    OllamaEmbeddingClient,
)
from internship_monitor.intelligence.providers import (
    IntelligenceProvider,
    OllamaHealthProvider,
    ProviderHealth,
    ProviderHealthStatus,
    provider_from_configuration,
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
    "EmbeddingAssessmentProvider",
    "EmbeddingCache",
    "EmbeddingProviderError",
    "IntelligenceProvider",
    "OllamaEmbeddingClient",
    "OllamaHealthProvider",
    "OllamaStructuredAssessmentClient",
    "ProviderHealth",
    "ProviderHealthStatus",
    "RoleArchetype",
    "StructuredAssessmentError",
    "StructuredLLMAssessmentProvider",
    "StructuredRoleVerdict",
    "cosine_similarity",
    "provider_from_configuration",
    "role_archetypes",
]
