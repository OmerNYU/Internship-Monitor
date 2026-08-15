from pathlib import Path
from unittest import TestCase

import httpx

from internship_monitor.analysis import DeterministicAssessor, IntelligenceTraceStatus
from internship_monitor.analysis.trace import IntelligenceStage, append_intelligence_stage
from internship_monitor.cli import _evaluation_intelligence_configuration
from internship_monitor.config import load_search_configuration
from internship_monitor.intelligence.diagnostics import ProbeCheck
from internship_monitor.intelligence.failures import (
    ProviderFailure,
    ProviderFailureCategory,
    failure_category,
)
from internship_monitor.models import JobListing

PROJECT_ROOT = Path(__file__).parents[1]


class IntelligenceDiagnosticsTests(TestCase):
    def setUp(self) -> None:
        self.configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")

    def test_evaluation_activation_is_an_immutable_in_memory_copy(self) -> None:
        activated = _evaluation_intelligence_configuration(self.configuration)
        self.assertFalse(self.configuration.intelligence.enabled)
        self.assertFalse(self.configuration.intelligence.agent.enabled)
        self.assertTrue(activated.intelligence.enabled)
        self.assertTrue(activated.intelligence.agent.enabled)
        self.assertEqual(
            activated.intelligence.embedding.model, self.configuration.intelligence.embedding.model
        )

    def test_failure_categories_are_safe_and_actionable(self) -> None:
        request = httpx.Request("GET", "http://127.0.0.1:11434/api/version")
        self.assertEqual(
            failure_category(httpx.ConnectError("refused", request=request)),
            ProviderFailureCategory.PROVIDER_UNREACHABLE,
        )
        self.assertEqual(
            failure_category(RuntimeError("configured model is not installed: qwen3:4b")),
            ProviderFailureCategory.MODEL_MISSING,
        )
        self.assertEqual(
            failure_category(RuntimeError("agent tool call has invalid arguments")),
            ProviderFailureCategory.TOOL_PROTOCOL_FAILURE,
        )
        self.assertEqual(
            failure_category(
                ProviderFailure("policy", ProviderFailureCategory.SEMANTIC_POLICY_REJECTED)
            ),
            ProviderFailureCategory.SEMANTIC_POLICY_REJECTED,
        )
        self.assertEqual(
            failure_category(
                ProviderFailure("grounding", ProviderFailureCategory.EVIDENCE_GROUNDING_FAILURE)
            ),
            ProviderFailureCategory.EVIDENCE_GROUNDING_FAILURE,
        )

    def test_invocation_trace_is_additive_and_has_no_private_payload(self) -> None:
        listing = JobListing.model_validate(
            {
                "source": "fixture",
                "source_job_id": "probe",
                "company": "Example",
                "title": "Technical Internship",
                "description": "Short test-only description.",
                "apply_url": "https://example.com/probe",
                "discovered_at": "2026-08-15T00:00:00Z",
            }
        )
        assessment = DeterministicAssessor(self.configuration).assess(listing)
        traced = append_intelligence_stage(
            assessment,
            stage=IntelligenceStage.EMBEDDING,
            status=IntelligenceTraceStatus.UNAVAILABLE,
            model="qwen3-embedding:0.6b",
            error_category=ProviderFailureCategory.PROVIDER_UNREACHABLE.value,
            invoked=True,
        )
        stage = traced.intelligence_trace.stages[-1]
        self.assertTrue(stage.invoked)
        self.assertEqual(stage.error_category, "provider_unreachable")
        self.assertEqual(stage.tool_names, ())
        self.assertEqual(stage.source_ids, ())

    def test_probe_check_only_carries_safe_summary_fields(self) -> None:
        check = ProbeCheck(
            configured_model="qwen3:4b",
            model_present=True,
            attempted=True,
            succeeded=True,
            vector_dimension=1024,
            finite_values=True,
            tool_names=("retrieve_role_archetypes",),
            retrieval_count=1,
            source_ids=("role:primary",),
        )
        self.assertEqual(check.source_ids, ("role:primary",))
        self.assertEqual(check.retrieval_count, 1)
