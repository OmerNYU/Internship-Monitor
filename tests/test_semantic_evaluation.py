import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import httpx

from internship_monitor.analysis import (
    DeterministicAssessor,
    RoleMatchLevel,
    SemanticAssessmentStatus,
)
from internship_monitor.cli import main
from internship_monitor.config import load_search_configuration
from internship_monitor.evaluation import evaluate_gold_cases, load_gold_cases
from internship_monitor.intelligence import (
    EmbeddingAssessmentProvider,
    EmbeddingCache,
    EmbeddingProviderError,
    OllamaEmbeddingClient,
    OllamaStructuredAssessmentClient,
    StructuredAssessmentError,
    StructuredLLMAssessmentProvider,
    StructuredRoleVerdict,
    cosine_similarity,
)
from internship_monitor.intelligence.failures import ProviderFailureCategory

PROJECT_ROOT = Path(__file__).parents[1]
FIXTURE = PROJECT_ROOT / "evaluation" / "gold.example.v1.jsonl"


class SemanticEvaluationTests(TestCase):
    def _enabled_configuration(self):
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        return configuration.model_copy(
            update={
                "intelligence": configuration.intelligence.model_copy(update={"enabled": True}),
            }
        )

    def _client(self, requests: list[httpx.Request], *, malformed: bool = False):
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "qwen3-embedding:0.6b"}]})
            if request.url.path == "/api/embed":
                if malformed:
                    return httpx.Response(200, json={"embeddings": [[1, float("nan")]]})
                payload = json.loads(request.content)
                vectors = [
                    [1.0, 0.0] if index < 2 else [0.0, 1.0]
                    for index, _ in enumerate(payload["input"])
                ]
                return httpx.Response(200, json={"embeddings": vectors})
            return httpx.Response(404)

        return OllamaEmbeddingClient(
            self._enabled_configuration().intelligence.ollama,
            "qwen3-embedding:0.6b",
            transport=httpx.MockTransport(handler),
        )

    def test_embedding_client_validates_model_response_cardinality_and_finite_vectors(self) -> None:
        requests: list[httpx.Request] = []
        client = self._client(requests)

        self.assertEqual(client.embed(("one", "two")), ((1.0, 0.0), (1.0, 0.0)))
        self.assertEqual([request.url.path for request in requests], ["/api/tags", "/api/embed"])

        bad_requests: list[httpx.Request] = []
        malformed = self._client(bad_requests, malformed=True)
        with self.assertRaises(EmbeddingProviderError):
            malformed.embed(("one",))

    def test_cache_is_normalized_and_isolated_by_model(self) -> None:
        with (
            TemporaryDirectory() as directory,
            EmbeddingCache(Path(directory) / "cache.sqlite3") as cache,
        ):
            cache.put("model-a", "  Example Text ", (1.0, 0.0))
            self.assertEqual(cache.get("model-a", "example text"), (1.0, 0.0))
            self.assertIsNone(cache.get("model-b", "example text"))

    def test_cosine_similarity_rejects_dimension_mismatches(self) -> None:
        self.assertEqual(cosine_similarity((1.0, 0.0), (1.0, 0.0)), 1.0)
        with self.assertRaises(EmbeddingProviderError):
            cosine_similarity((1.0,), (1.0, 0.0))

    def test_embedding_provider_promotes_only_ambiguous_unblocked_student_roles(self) -> None:
        configuration = self._enabled_configuration()
        baseline = DeterministicAssessor(configuration)
        listing = load_gold_cases(FIXTURE)[0].listing.model_copy(
            update={
                "source_job_id": "semantic-ambiguous",
                "title": "Platform Discovery Intern",
                "description": "Student internship building Python developer tooling and APIs.",
            }
        )
        requests: list[httpx.Request] = []
        provider = EmbeddingAssessmentProvider(
            configuration,
            baseline=baseline,
            cache_path=None,
            client=self._client(requests),
        )

        assessment = provider.assess(listing)

        self.assertFalse(assessment.is_hard_blocked)
        self.assertEqual(assessment.role.level, RoleMatchLevel.RELEVANT)
        assert assessment.semantic is not None
        self.assertEqual(assessment.semantic.status, SemanticAssessmentStatus.APPLIED)
        self.assertEqual(assessment.semantic.original_role_level, "not_relevant")
        self.assertEqual(assessment.semantic.proposed_role_level, "relevant")
        self.assertEqual([request.url.path for request in requests], ["/api/tags", "/api/embed"])

    def test_hard_blocked_listing_skips_embedding_request(self) -> None:
        configuration = self._enabled_configuration()
        configuration = configuration.model_copy(
            update={
                "regional_strategy": configuration.regional_strategy.model_copy(
                    update={"hard_excluded_countries": ("Singapore",)}
                )
            }
        )
        requests: list[httpx.Request] = []
        provider = EmbeddingAssessmentProvider(
            configuration,
            baseline=DeterministicAssessor(configuration),
            cache_path=None,
            client=self._client(requests),
        )

        assessment = provider.assess(load_gold_cases(FIXTURE)[0].listing)

        self.assertTrue(assessment.is_hard_blocked)
        assert assessment.semantic is not None
        self.assertEqual(assessment.semantic.status, SemanticAssessmentStatus.SKIPPED_HARD_BLOCKED)
        self.assertEqual(requests, [])

    def test_embedding_fallback_is_visible_and_preserves_the_deterministic_vector(self) -> None:
        configuration = self._enabled_configuration()
        baseline = DeterministicAssessor(configuration)
        listing = load_gold_cases(FIXTURE)[0].listing.model_copy(
            update={
                "source_job_id": "semantic-fallback",
                "title": "Platform Discovery Intern",
                "description": "Student internship building Python developer tooling and APIs.",
            }
        )
        provider = EmbeddingAssessmentProvider(
            configuration,
            baseline=baseline,
            cache_path=None,
            client=self._client([], malformed=True),
        )

        actual = provider.assess(listing)
        expected = baseline.assess(listing)

        self.assertEqual(actual.role, expected.role)
        self.assertEqual(actual.score, expected.score)
        self.assertEqual(actual.hard_blockers, expected.hard_blockers)
        assert actual.semantic is not None
        self.assertEqual(actual.semantic.status, SemanticAssessmentStatus.FALLBACK)

    def test_cached_embedding_evaluation_has_no_second_provider_call_and_keeps_retention_safe(
        self,
    ) -> None:
        configuration = self._enabled_configuration()
        baseline = DeterministicAssessor(configuration)
        listing = load_gold_cases(FIXTURE)[0].listing.model_copy(
            update={
                "source_job_id": "semantic-cache",
                "title": "Platform Discovery Intern",
                "description": "Student internship building Python developer tooling and APIs.",
            }
        )
        requests: list[httpx.Request] = []
        with TemporaryDirectory() as directory:
            provider = EmbeddingAssessmentProvider(
                configuration,
                baseline=baseline,
                cache_path=Path(directory) / "embeddings.sqlite3",
                client=self._client(requests),
            )
            first = provider.assess(listing)
            first_requests = len(requests)
            second = provider.assess(listing)

        self.assertEqual(first.role, second.role)
        self.assertEqual(len(requests), first_requests)
        report = evaluate_gold_cases((load_gold_cases(FIXTURE)[0],), provider)
        self.assertEqual(report.expected_retained_incorrectly_blocked, 0)

    def test_structured_client_uses_schema_and_validates_its_response(self) -> None:
        listing = load_gold_cases(FIXTURE)[0].listing.model_copy(
            update={
                "source_job_id": "structured-client",
                "title": "Platform Discovery Intern",
                "description": "Student internship building Python developer tooling and APIs.",
            }
        )
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}]})
            if request.url.path == "/api/chat":
                return httpx.Response(
                    200,
                    json={
                        "message": {
                            "content": json.dumps(
                                {
                                    "role_level": "relevant",
                                    "confidence": 0.9,
                                    "evidence": ["Python developer tooling"],
                                }
                            )
                        }
                    },
                )
            return httpx.Response(404)

        client = OllamaStructuredAssessmentClient(
            self._enabled_configuration(),
            transport=httpx.MockTransport(handler),
        )

        verdict = client.assess(listing)

        self.assertEqual(verdict.role_level, RoleMatchLevel.RELEVANT)
        chat_payload = json.loads(requests[1].content)
        self.assertFalse(chat_payload["stream"])
        self.assertFalse(chat_payload["think"])
        self.assertEqual(chat_payload["options"], {"temperature": 0})
        self.assertIn("properties", chat_payload["format"])

    def test_structured_provider_requires_grounded_evidence_and_preserves_fallback(self) -> None:
        configuration = self._enabled_configuration()
        baseline = DeterministicAssessor(configuration)
        listing = load_gold_cases(FIXTURE)[0].listing.model_copy(
            update={
                "source_job_id": "structured-fallback",
                "title": "Platform Discovery Intern",
                "description": "Student internship building Python developer tooling and APIs.",
            }
        )

        class UngroundedClient:
            def assess(self, _: object) -> StructuredRoleVerdict:
                return StructuredRoleVerdict(
                    role_level=RoleMatchLevel.RELEVANT,
                    confidence=0.95,
                    evidence=("invented evidence",),
                )

        actual = StructuredLLMAssessmentProvider(
            configuration,
            baseline=baseline,
            client=UngroundedClient(),
        ).assess(listing)
        expected = baseline.assess(listing)

        self.assertEqual(actual.role, expected.role)
        assert actual.semantic is not None
        self.assertEqual(actual.semantic.status, SemanticAssessmentStatus.FALLBACK)
        self.assertEqual(
            actual.semantic.error_category,
            ProviderFailureCategory.EVIDENCE_GROUNDING_FAILURE.value,
        )

    def test_structured_policy_rejection_preserves_deterministic_assessment(self) -> None:
        configuration = self._enabled_configuration()
        baseline = DeterministicAssessor(configuration)
        listing = load_gold_cases(FIXTURE)[0].listing.model_copy(
            update={
                "source_job_id": "structured-policy",
                "title": "Platform Discovery Intern",
                "description": "Student internship building Python developer tooling and APIs.",
            }
        )

        class NonPromotionClient:
            def assess(self, _: object) -> StructuredRoleVerdict:
                return StructuredRoleVerdict(
                    role_level=RoleMatchLevel.NOT_RELEVANT,
                    confidence=0.95,
                    evidence=("Python developer tooling",),
                )

        actual = StructuredLLMAssessmentProvider(
            configuration,
            baseline=baseline,
            client=NonPromotionClient(),
        ).assess(listing)
        expected = baseline.assess(listing)

        self.assertEqual(actual.role, expected.role)
        assert actual.semantic is not None
        self.assertEqual(actual.semantic.status, SemanticAssessmentStatus.FALLBACK)
        self.assertEqual(
            actual.semantic.error_category,
            ProviderFailureCategory.SEMANTIC_POLICY_REJECTED.value,
        )

    def test_structured_provider_promotes_and_skips_hard_blocked_listings(self) -> None:
        configuration = self._enabled_configuration()
        baseline = DeterministicAssessor(configuration)
        listing = load_gold_cases(FIXTURE)[0].listing.model_copy(
            update={
                "source_job_id": "structured-promotion",
                "title": "Platform Discovery Intern",
                "description": "Student internship building Python developer tooling and APIs.",
            }
        )

        class GroundedClient:
            def assess(self, _: object) -> StructuredRoleVerdict:
                return StructuredRoleVerdict(
                    role_level=RoleMatchLevel.RELEVANT,
                    confidence=0.95,
                    evidence=("Python developer tooling",),
                )

        provider = StructuredLLMAssessmentProvider(
            configuration,
            baseline=baseline,
            client=GroundedClient(),
        )
        assessment = provider.assess(listing)

        self.assertEqual(assessment.role.level, RoleMatchLevel.RELEVANT)
        assert assessment.semantic is not None
        self.assertEqual(assessment.semantic.status, SemanticAssessmentStatus.APPLIED)

        blocked_configuration = configuration.model_copy(
            update={
                "regional_strategy": configuration.regional_strategy.model_copy(
                    update={"hard_excluded_countries": ("Singapore",)}
                )
            }
        )
        blocked = StructuredLLMAssessmentProvider(
            blocked_configuration,
            baseline=DeterministicAssessor(blocked_configuration),
            client=GroundedClient(),
        ).assess(load_gold_cases(FIXTURE)[0].listing)
        self.assertTrue(blocked.is_hard_blocked)
        assert blocked.semantic is not None
        self.assertEqual(blocked.semantic.status, SemanticAssessmentStatus.SKIPPED_HARD_BLOCKED)

    def test_structured_client_rejects_invalid_schema_and_missing_model(self) -> None:
        listing = load_gold_cases(FIXTURE)[0].listing

        def missing_model(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"models": []})

        client = OllamaStructuredAssessmentClient(
            self._enabled_configuration(),
            transport=httpx.MockTransport(missing_model),
        )

        with self.assertRaises(StructuredAssessmentError):
            client.assess(listing)

        def invalid_schema(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}]})
            return httpx.Response(
                200,
                json={
                    "message": {
                        "content": json.dumps(
                            {
                                "role_level": "relevant",
                                "confidence": 0.95,
                                "evidence": ["Python"],
                                "unexpected": "rejected",
                            }
                        )
                    }
                },
            )

        invalid_client = OllamaStructuredAssessmentClient(
            self._enabled_configuration(),
            transport=httpx.MockTransport(invalid_schema),
        )
        with self.assertRaises(StructuredAssessmentError):
            invalid_client.assess(listing)

    def test_llm_evaluate_cli_is_offline_when_intelligence_is_disabled(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["evaluate", "--dataset", str(FIXTURE), "--provider", "llm", "--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["provider_name"], "llm")
        self.assertEqual(payload["expected_retained_incorrectly_blocked"], 0)
        self.assertIn("fallback", {case["semantic_status"] for case in payload["cases"]})
