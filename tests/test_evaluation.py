import json
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.analysis import (
    DeterministicAssessor,
    HardBlocker,
    HardBlockerKind,
    OpportunityStrength,
)
from internship_monitor.cli import main
from internship_monitor.config import load_search_configuration
from internship_monitor.evaluation import (
    GoldDatasetError,
    evaluate_gold_cases,
    load_gold_cases,
)
from internship_monitor.evaluation.models import GoldCase

PROJECT_ROOT = Path(__file__).parents[1]
FIXTURE = PROJECT_ROOT / "evaluation" / "gold.example.v1.jsonl"


class BlockingProvider:
    name = "controlled-blocker"

    def __init__(self, baseline: DeterministicAssessor) -> None:
        self._baseline = baseline

    def assess(self, listing):
        assessment = self._baseline.assess(listing)
        return replace(
            assessment,
            hard_blockers=(
                HardBlocker(
                    kind=HardBlockerKind.HARD_EXCLUDED_LOCATION,
                    reason="Controlled benchmark divergence.",
                    evidence=("test",),
                ),
            ),
            strength=OpportunityStrength.BLOCKED,
        )


class GoldDatasetTests(TestCase):
    def test_tracked_synthetic_dataset_is_valid_and_matches_deterministic_baseline(self) -> None:
        cases = load_gold_cases(FIXTURE)
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")

        report = evaluate_gold_cases(cases, DeterministicAssessor(configuration))

        self.assertEqual(len(cases), 10)
        self.assertEqual(report.mismatch_count, 0)
        self.assertEqual(report.expected_retained_incorrectly_blocked, 0)
        self.assertEqual(report.hard_blocker_metrics.recall, 1.0)

    def test_loader_rejects_unknown_fields_duplicate_ids_and_invalid_label_relationships(
        self,
    ) -> None:
        valid = json.loads(FIXTURE.read_text(encoding="utf-8").splitlines()[0])
        duplicate = json.dumps(valid)
        unknown = dict(valid)
        unknown["unexpected"] = "value"
        invalid = dict(valid)
        invalid["expected"] = dict(valid["expected"])
        invalid["expected"]["actionability"] = "blocked"
        wrong_version = dict(valid)
        wrong_version["schema_version"] = 2

        with TemporaryDirectory() as directory:
            duplicate_path = Path(directory) / "duplicate.jsonl"
            duplicate_path.write_text(f"{json.dumps(valid)}\n{duplicate}\n", encoding="utf-8")
            unknown_path = Path(directory) / "unknown.jsonl"
            unknown_path.write_text(f"{json.dumps(unknown)}\n", encoding="utf-8")
            invalid_path = Path(directory) / "invalid.jsonl"
            invalid_path.write_text(f"{json.dumps(invalid)}\n", encoding="utf-8")
            version_path = Path(directory) / "version.jsonl"
            version_path.write_text(f"{json.dumps(wrong_version)}\n", encoding="utf-8")

            with self.assertRaisesRegex(GoldDatasetError, "duplicate gold case_id"):
                load_gold_cases(duplicate_path)
            with self.assertRaisesRegex(GoldDatasetError, "unexpected"):
                load_gold_cases(unknown_path)
            with self.assertRaisesRegex(GoldDatasetError, "hard_blocker_kind"):
                load_gold_cases(invalid_path)
            with self.assertRaisesRegex(GoldDatasetError, "schema_version"):
                load_gold_cases(version_path)

    def test_gold_case_can_cover_a_configured_hard_exclusion(self) -> None:
        fixture_case = load_gold_cases(FIXTURE)[0]
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        configuration = configuration.model_copy(
            update={
                "regional_strategy": configuration.regional_strategy.model_copy(
                    update={"hard_excluded_countries": ("India",)}
                )
            }
        )
        case = GoldCase.model_validate(
            {
                "case_id": "configured-hard-exclusion",
                "listing": fixture_case.listing.model_dump(mode="json")
                | {
                    "source_job_id": "configured-hard-exclusion",
                    "location": "Bengaluru, India",
                },
                "expected": {
                    "actionability": "blocked",
                    "hard_blocker_kinds": ["hard_excluded_location"],
                    "role_level": "strong_match",
                    "geographic_bucket": "blocked",
                    "graduation_status": "compatible",
                    "authorization_status": "positive_support_signal",
                    "language_status": "compatible",
                    "season_status": "compatible",
                    "strength": "blocked",
                },
            }
        )

        report = evaluate_gold_cases((case,), DeterministicAssessor(configuration))

        self.assertEqual(report.mismatch_count, 0)
        self.assertEqual(report.hard_blocker_metrics.precision, 1.0)

    def test_injected_provider_reports_mismatches_and_retention_safety_failure(self) -> None:
        case = load_gold_cases(FIXTURE)[0]
        baseline = DeterministicAssessor(
            load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        )

        report = evaluate_gold_cases((case,), BlockingProvider(baseline))

        self.assertEqual(report.provider_name, "controlled-blocker")
        self.assertEqual(report.expected_retained_incorrectly_blocked, 1)
        self.assertEqual(
            tuple(mismatch.field for mismatch in report.cases[0].mismatches),
            ("actionability", "strength", "hard_blocker_kinds"),
        )
        self.assertEqual(report.hard_blocker_metrics.false_positives, 1)

    def test_evaluate_cli_is_offline_and_supports_text_and_json_reports(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["evaluate", "--dataset", str(FIXTURE)])

        self.assertEqual(exit_code, 0)
        self.assertIn("Evaluation complete: provider=deterministic, cases=10", output.getvalue())
        self.assertIn("Retained cases incorrectly blocked: 0", output.getvalue())

        json_output = StringIO()
        with redirect_stdout(json_output):
            exit_code = main(["evaluate", "--dataset", str(FIXTURE), "--json"])

        payload = json.loads(json_output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["provider_name"], "deterministic")
        self.assertEqual(payload["expected_retained_incorrectly_blocked"], 0)
