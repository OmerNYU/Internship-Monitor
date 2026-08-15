from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.analysis import (
    DeterministicAssessor,
    HardBlocker,
    HardBlockerKind,
    IntelligenceTraceStatus,
    RoleAssessment,
    RoleMatchLevel,
)
from internship_monitor.analysis.trace import IntelligenceStage, append_intelligence_stage
from internship_monitor.config import load_search_configuration
from internship_monitor.evaluation.ablation import (
    format_ablation_markdown,
    run_ablation,
    write_ablation_artifacts,
)
from internship_monitor.evaluation.models import HumanGoldCase

PROJECT_ROOT = Path(__file__).parents[1]


def _case(
    case_id: str,
    relevance: str,
    hard_block: bool | str = False,
    *,
    title: str = "Platform Opportunity",
    role_family: str = "unknown",
) -> HumanGoldCase:
    expected = {
        "relevance": relevance,
        "hard_block": hard_block,
        "authorization": "unknown",
        "role_family": role_family,
    }
    if hard_block is True:
        expected["blocker_reason"] = "incompatible_season"
    return HumanGoldCase.model_validate(
        {
            "schema_version": "human_gold_v1",
            "case_id": case_id,
            "source_identity": f"fixture:{case_id}",
            "listing": {
                "source": "fixture",
                "source_job_id": case_id,
                "company": "Example Company",
                "title": title,
                "description": "Technical opportunity for candidates.",
                "apply_url": f"https://example.com/{case_id}",
                "discovered_at": "2026-08-15T10:00:00Z",
            },
            "expected": expected,
            "human_rationale": "Independent human label.",
            "labeling_provenance": "human",
        }
    )


class FixedProvider:
    name = "fixed"

    def __init__(self, assessments):
        self._assessments = assessments

    def assess(self, listing):
        return self._assessments[listing.source_job_id]


class Clock:
    def __init__(self):
        self._value = 0.0

    def __call__(self):
        self._value += 0.01
        return self._value


class AblationTests(TestCase):
    def setUp(self):
        self.configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        self.cases = (_case("relevant", "relevant"), _case("irrelevant", "irrelevant"))
        baseline = DeterministicAssessor(self.configuration)
        self.base = {
            case.case_id: replace(baseline.assess(case.listing), hard_blockers=())
            for case in self.cases
        }

    def _role(self, assessment, level):
        return replace(
            assessment,
            role=RoleAssessment(level, "test", (), (), ()),
        )

    def _assessments(self, cases, levels, blockers=None):
        blockers = blockers or {}
        baseline = DeterministicAssessor(self.configuration)
        return {
            case.case_id: replace(
                self._role(baseline.assess(case.listing), levels[case.case_id]),
                hard_blockers=blockers.get(case.case_id, ()),
            )
            for case in cases
        }

    def test_recall_false_negatives_promotions_regressions_and_unknown_dimensions(self):
        deterministic = FixedProvider(
            {
                "relevant": self._role(self.base["relevant"], RoleMatchLevel.NOT_RELEVANT),
                "irrelevant": self._role(self.base["irrelevant"], RoleMatchLevel.NOT_RELEVANT),
            }
        )
        promoted = FixedProvider(
            {
                "relevant": self._role(self.base["relevant"], RoleMatchLevel.RELEVANT),
                "irrelevant": self._role(self.base["irrelevant"], RoleMatchLevel.RELEVANT),
            }
        )
        before = tuple(case.model_dump_json() for case in self.cases)
        report = run_ablation(
            self.cases,
            (("deterministic", deterministic), ("embedding", promoted)),
            self.configuration,
            clock=Clock(),
            generated_at="2026-08-15T00:00:00+00:00",
        )
        baseline, embedding = report.providers
        self.assertEqual(before, tuple(case.model_dump_json() for case in self.cases))
        self.assertEqual(
            tuple(item.case_id for item in baseline.cases),
            tuple(item.case_id for item in embedding.cases),
        )
        self.assertEqual(baseline.relevant_recall, (0, 1))
        self.assertEqual(baseline.false_negative_ids, ("relevant",))
        self.assertEqual(dict(embedding.promotion_summary)["beneficial"], ("relevant",))
        self.assertEqual(dict(embedding.promotion_summary)["harmful"], ("irrelevant",))
        self.assertEqual(dict(embedding.promotion_summary)["regression"], ("irrelevant",))
        self.assertEqual(dict(embedding.latency_ms)["p50"], 10.0)
        agreements = {name: (matches, total) for name, matches, total in embedding.field_agreement}
        self.assertEqual(agreements["authorization"], (0, 0))

    def test_semantic_and_final_dimensions_keep_blocked_target_role_separate(self):
        cases = (
            _case(
                "unblocked",
                "relevant",
                title="Software Engineer Intern",
                role_family="software_engineering",
            ),
            _case(
                "blocked",
                "irrelevant",
                True,
                title="Software Engineer Intern (Fall 2026)",
                role_family="software_engineering",
            ),
        )
        blocker = HardBlocker(HardBlockerKind.INCOMPATIBLE_SEASON, "Fall 2026", ("Fall 2026",))
        provider = FixedProvider(
            self._assessments(
                cases,
                {"unblocked": RoleMatchLevel.STRONG_MATCH, "blocked": RoleMatchLevel.STRONG_MATCH},
                {"blocked": (blocker,)},
            )
        )
        result = run_ablation(cases, (("deterministic", provider),), self.configuration).providers[
            0
        ]
        self.assertEqual(result.semantic_role.evaluable_cases, 2)
        self.assertEqual(result.semantic_role.strict_recall, (2, 2))
        self.assertEqual(result.final_opportunity.strict_recall, (1, 1))
        self.assertEqual(result.final_opportunity.strict_false_positive_ids, ())
        self.assertEqual(result.safety.incorrect_hard_block_ids, ())
        self.assertEqual(result.safety.missed_human_blocker_ids, ())
        blocked = next(item for item in result.cases if item.case_id == "blocked")
        self.assertNotIn("final_false_positive", blocked.difference_categories)

    def test_missed_blocker_is_final_false_positive_even_when_semantic_role_is_correct(self):
        case = _case(
            "missed",
            "irrelevant",
            True,
            title="Software Engineer Intern (Fall 2026)",
            role_family="software_engineering",
        )
        provider = FixedProvider(
            self._assessments((case,), {"missed": RoleMatchLevel.STRONG_MATCH})
        )
        result = run_ablation(
            (case,), (("deterministic", provider),), self.configuration
        ).providers[0]
        self.assertEqual(result.semantic_role.strict_recall, (1, 1))
        self.assertEqual(result.final_opportunity.strict_false_positive_ids, ("missed",))
        self.assertEqual(result.safety.missed_human_blocker_ids, ("missed",))

    def test_irrelevant_internship_and_incorrect_block_remain_separate_safety_results(self):
        cases = (
            _case("marketing", "irrelevant", title="Marketing Intern"),
            _case("blocked_marketing", "irrelevant", title="Marketing Intern"),
        )
        blocker = HardBlocker(HardBlockerKind.HARD_EXCLUDED_LOCATION, "test", ("test",))
        provider = FixedProvider(
            self._assessments(
                cases,
                {
                    "marketing": RoleMatchLevel.NOT_RELEVANT,
                    "blocked_marketing": RoleMatchLevel.NOT_RELEVANT,
                },
                {"blocked_marketing": (blocker,)},
            )
        )
        result = run_ablation(cases, (("deterministic", provider),), self.configuration).providers[
            0
        ]
        self.assertEqual(result.semantic_role.strict_false_positive_ids, ())
        self.assertEqual(result.final_opportunity.strict_false_positive_ids, ())
        self.assertEqual(result.safety.incorrect_hard_block_ids, ("blocked_marketing",))
        blocked = next(item for item in result.cases if item.case_id == "blocked_marketing")
        self.assertIn("incorrect_hard_block", blocked.difference_categories)
        self.assertNotIn("final_false_positive", blocked.difference_categories)

    def test_provider_effects_distinguish_unblocked_and_blocked_semantic_promotions(self):
        cases = (
            _case(
                "rescue",
                "relevant",
                title="Software Engineer Intern",
                role_family="software_engineering",
            ),
            _case(
                "blocked_rescue",
                "irrelevant",
                True,
                title="Software Engineer Intern (Fall 2026)",
                role_family="software_engineering",
            ),
            _case("harmful", "irrelevant", title="Marketing Intern"),
        )
        blocker = HardBlocker(HardBlockerKind.INCOMPATIBLE_SEASON, "Fall 2026", ("Fall 2026",))
        deterministic = FixedProvider(
            self._assessments(
                cases,
                {case.case_id: RoleMatchLevel.NOT_RELEVANT for case in cases},
                {"blocked_rescue": (blocker,)},
            )
        )
        embedding = FixedProvider(
            self._assessments(
                cases,
                {case.case_id: RoleMatchLevel.RELEVANT for case in cases},
                {"blocked_rescue": (blocker,)},
            )
        )
        result = run_ablation(
            cases,
            (("deterministic", deterministic), ("embedding", embedding)),
            self.configuration,
        ).providers[1]
        effects = result.provider_effects
        self.assertEqual(effects.semantic_beneficial_promotions, ("rescue", "blocked_rescue"))
        self.assertEqual(effects.blocked_semantic_promotions, ("blocked_rescue",))
        self.assertEqual(effects.final_beneficial_changes, ("rescue",))
        self.assertEqual(effects.semantic_harmful_promotions, ("harmful",))
        self.assertEqual(effects.final_harmful_changes, ("harmful",))
        self.assertNotIn("blocked_rescue", result.final_opportunity.strict_false_positive_ids)

    def test_semantic_indeterminate_blocked_internship_is_not_guessed(self):
        case = _case("accounting", "irrelevant", True, title="Accounting Intern")
        blocker = HardBlocker(HardBlockerKind.INCOMPATIBLE_SEASON, "Fall 2026", ("Fall 2026",))
        provider = FixedProvider(
            self._assessments(
                (case,), {"accounting": RoleMatchLevel.NOT_RELEVANT}, {"accounting": (blocker,)}
            )
        )
        result = run_ablation(
            (case,), (("deterministic", provider),), self.configuration
        ).providers[0]
        self.assertEqual(result.semantic_role.evaluable_cases, 0)
        self.assertEqual(result.semantic_role.indeterminate_case_ids, ("accounting",))
        self.assertEqual(result.final_opportunity.evaluable_cases, 1)

    def test_review_is_broad_positive_but_not_strict_positive(self):
        case = _case("review", "maybe", title="Technical Product Internship")
        provider = FixedProvider(self._assessments((case,), {"review": RoleMatchLevel.REVIEW}))
        result = run_ablation(
            (case,), (("deterministic", provider),), self.configuration
        ).providers[0]
        self.assertEqual(result.semantic_role.strict_recall, (0, 0))
        self.assertEqual(result.semantic_role.broad_recall, (1, 1))
        self.assertEqual(result.final_opportunity.broad_recall, (1, 1))

    def test_safe_provider_trace_retains_success_before_fallback(self):
        succeeded = append_intelligence_stage(
            self.base["relevant"],
            stage=IntelligenceStage.EMBEDDING,
            status=IntelligenceTraceStatus.SUCCEEDED,
            model="local-embed",
        )
        fallback = append_intelligence_stage(
            succeeded,
            stage=IntelligenceStage.STRUCTURED_LLM,
            status=IntelligenceTraceStatus.UNAVAILABLE,
            model="local-llm",
            error_category="timeout",
        )
        provider = FixedProvider({"relevant": fallback, "irrelevant": fallback})
        report = run_ablation(
            self.cases,
            (("structured_llm", provider),),
            self.configuration,
            generated_at="2026-08-15T00:00:00+00:00",
        )
        result = report.providers[0]
        self.assertIn(("embedding", "succeeded", 2), result.stage_statuses)
        self.assertIn(("structured_llm", "unavailable", 2), result.stage_statuses)
        self.assertEqual(result.error_categories, (("timeout", 2),))
        self.assertEqual(result.tool_metrics, (("tool_calls", 0), ("retrievals", 0)))

    def test_incorrect_hard_blocks_and_safe_artifacts_are_reported(self):
        blocker = HardBlocker(HardBlockerKind.HARD_EXCLUDED_LOCATION, "test", ("test",))
        blocked = FixedProvider(
            {
                "relevant": replace(self.base["relevant"], hard_blockers=(blocker,)),
                "irrelevant": self.base["irrelevant"],
            }
        )
        report = run_ablation(
            self.cases,
            (("deterministic", blocked),),
            self.configuration,
            generated_at="2026-08-15T00:00:00+00:00",
        )
        provider = report.providers[0]
        self.assertEqual(provider.incorrect_hard_block_ids, ("relevant",))
        self.assertEqual(provider.false_negative_ids, ("relevant",))
        self.assertEqual(provider.safety.incorrect_hard_block_ids, ("relevant",))
        markdown = format_ablation_markdown(report)
        self.assertIn("final_false_negative", markdown)
        self.assertIn("relevant", markdown)
        self.assertNotIn("Technical opportunity for candidates.", markdown)
        with TemporaryDirectory() as directory:
            output, text = Path(directory) / "report.json", Path(directory) / "report.md"
            write_ablation_artifacts(report, output, text)
            self.assertEqual(text.read_text(encoding="utf-8"), markdown)
            artifact = output.read_text(encoding="utf-8")
            self.assertIn('"semantic_role"', artifact)
            self.assertIn('"final_opportunity"', artifact)
            self.assertNotIn("Technical opportunity for candidates.", artifact)

    def test_limitations_use_actual_dataset_case_count(self):
        report = run_ablation(
            self.cases,
            (("deterministic", FixedProvider(self.base)),),
            self.configuration,
            generated_at="2026-08-15T00:00:00+00:00",
        )
        self.assertTrue(any("2-case pilot" in item for item in report.limitations))
