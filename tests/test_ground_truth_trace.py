import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import httpx

from internship_monitor.analysis import DeterministicAssessor, RoleMatchLevel
from internship_monitor.analysis.trace import (
    IntelligenceStage,
    IntelligenceTraceStatus,
    append_intelligence_stage,
)
from internship_monitor.config import load_search_configuration
from internship_monitor.evaluation import (
    GoldDatasetError,
    HumanLabelState,
    curate_human_label_templates,
    evaluate_human_gold_cases,
    load_gold_cases,
    load_human_gold_cases,
)
from internship_monitor.intelligence import (
    AgenticAdjudicationProvider,
    EmbeddingAssessmentProvider,
    OllamaEmbeddingClient,
    StructuredAssessmentError,
    StructuredLLMAssessmentProvider,
)

PROJECT_ROOT = Path(__file__).parents[1]
REGRESSION_FIXTURE = PROJECT_ROOT / "evaluation" / "gold.example.v1.jsonl"
HUMAN_FIXTURE = PROJECT_ROOT / "evaluation" / "human_gold.example.v1.jsonl"


class RaisingStructuredClient:
    def assess(self, listing):
        raise StructuredAssessmentError("structured response schema is invalid")


class EmptyRetriever:
    def retrieve(self, query, *, kinds=None, limit=4):
        return ()


class StaticProvider:
    name = "static"

    def __init__(self, assessment):
        self._assessment = assessment

    def assess(self, listing):
        return self._assessment


class GroundTruthAndTraceTests(TestCase):
    def _enabled_configuration(self):
        config = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        return config.model_copy(
            update={"intelligence": config.intelligence.model_copy(update={"enabled": True})}
        )

    def test_regression_and_sanitized_human_datasets_remain_separate_and_valid(self) -> None:
        self.assertEqual(len(load_gold_cases(REGRESSION_FIXTURE)), 10)
        cases = load_human_gold_cases(HUMAN_FIXTURE)
        self.assertEqual(cases[0].labeling_provenance.value, "human")
        self.assertEqual(cases[0].expected.authorization, HumanLabelState.NOT_LABELED)
        self.assertNotIn("omer", HUMAN_FIXTURE.read_text(encoding="utf-8").casefold())
        self.assertIn(
            "evaluation.local/", (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        )

    def test_human_schema_rejects_malformed_labels(self) -> None:
        payload = json.loads(HUMAN_FIXTURE.read_text(encoding="utf-8"))
        payload["expected"]["hard_block"] = True
        payload["expected"]["blocker_reason"] = "not_labeled"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(GoldDatasetError, "blocker_reason"):
                load_human_gold_cases(path)

    def test_curation_is_deterministic_and_emits_no_expected_human_answer(self) -> None:
        config = self._enabled_configuration()
        with TemporaryDirectory() as directory:
            first, second = Path(directory) / "one.jsonl", Path(directory) / "two.jsonl"
            self.assertEqual(
                curate_human_label_templates(REGRESSION_FIXTURE, first, config, limit=5, seed=26),
                5,
            )
            curate_human_label_templates(REGRESSION_FIXTURE, second, config, limit=5, seed=26)
            self.assertEqual(first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8"))
            record = json.loads(first.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["expected"]["relevance"], "not_labeled")
            self.assertEqual(record["expected"]["hard_block"], "not_labeled")
            self.assertEqual(record["labeling_provenance"], "template")
            with self.assertRaisesRegex(GoldDatasetError, "unreviewed curation template"):
                load_human_gold_cases(first)
            self.assertEqual(len(load_human_gold_cases(first, allow_templates=True)), 5)

    def test_embedding_success_is_preserved_when_llm_falls_back(self) -> None:
        config = self._enabled_configuration()
        listing = load_gold_cases(REGRESSION_FIXTURE)[0].listing.model_copy(
            update={
                "title": "Discovery Intern",
                "description": "Student internship building Python APIs and tooling.",
            }
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "qwen3-embedding:0.6b"}]})
            count = len(json.loads(request.content)["input"])
            vectors = [[1.0, 0.0]] + [[0.7, 0.714143] for _ in range(count - 1)]
            return httpx.Response(200, json={"embeddings": vectors})

        embedding = EmbeddingAssessmentProvider(
            config,
            baseline=DeterministicAssessor(config),
            client=OllamaEmbeddingClient(
                config.intelligence.ollama,
                config.intelligence.embedding.model,
                transport=httpx.MockTransport(handler),
            ),
        )
        result = StructuredLLMAssessmentProvider(
            config, baseline=embedding, client=RaisingStructuredClient()
        ).assess(listing)
        stages = {stage.stage: stage.status for stage in result.intelligence_trace.stages}
        self.assertEqual(stages["embedding"], IntelligenceTraceStatus.SUCCEEDED)
        self.assertEqual(stages["structured_llm"], IntelligenceTraceStatus.INVALID_OUTPUT)

    def test_skipped_and_unavailable_trace_states_remain_distinct(self) -> None:
        config = self._enabled_configuration()
        blocked_config = config.model_copy(
            update={
                "regional_strategy": config.regional_strategy.model_copy(
                    update={"hard_excluded_countries": ("Singapore",)}
                )
            }
        )
        blocked = EmbeddingAssessmentProvider(
            blocked_config, baseline=DeterministicAssessor(blocked_config)
        ).assess(load_gold_cases(REGRESSION_FIXTURE)[0].listing)
        self.assertEqual(
            blocked.intelligence_trace.stages[-1].status, IntelligenceTraceStatus.SKIPPED
        )

        def unavailable(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        listing = load_gold_cases(REGRESSION_FIXTURE)[0].listing.model_copy(
            update={"title": "Discovery Intern", "description": "Student internship Python API."}
        )
        fallback = EmbeddingAssessmentProvider(
            config,
            baseline=DeterministicAssessor(config),
            client=OllamaEmbeddingClient(
                config.intelligence.ollama,
                config.intelligence.embedding.model,
                transport=httpx.MockTransport(unavailable),
            ),
        ).assess(listing)
        self.assertEqual(
            fallback.intelligence_trace.stages[-1].status,
            IntelligenceTraceStatus.UNAVAILABLE,
        )

    def test_agent_fallback_preserves_prior_llm_success_and_safe_trace(self) -> None:
        config = self._enabled_configuration()
        config = config.model_copy(
            update={
                "intelligence": config.intelligence.model_copy(
                    update={
                        "agent": config.intelligence.agent.model_copy(update={"enabled": False})
                    }
                )
            }
        )
        listing = load_gold_cases(REGRESSION_FIXTURE)[-1].listing
        assessment = DeterministicAssessor(config).assess(listing)
        assessment = append_intelligence_stage(
            assessment,
            stage=IntelligenceStage.STRUCTURED_LLM,
            status=IntelligenceTraceStatus.SUCCEEDED,
            prior_role_level=RoleMatchLevel.NOT_RELEVANT.value,
            model="qwen3:4b",
        )
        result = AgenticAdjudicationProvider(
            config, baseline=StaticProvider(assessment), retriever=EmptyRetriever()
        ).assess(listing)
        stages = {stage.stage: stage.status for stage in result.intelligence_trace.stages}
        self.assertEqual(stages["structured_llm"], IntelligenceTraceStatus.SUCCEEDED)
        self.assertEqual(stages["agent"], IntelligenceTraceStatus.FALLBACK)
        self.assertEqual(stages["rag"], IntelligenceTraceStatus.NOT_RUN)
        self.assertTrue(
            all("excerpt" not in stage.source_ids for stage in result.intelligence_trace.stages)
        )
        report = evaluate_human_gold_cases(
            load_human_gold_cases(HUMAN_FIXTURE), StaticProvider(result)
        )
        self.assertEqual(report.cases[0].stage_count, len(result.intelligence_trace.stages))
