import json
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.cli import main
from internship_monitor.config import load_search_configuration
from internship_monitor.evaluation import (
    HumanLabelState,
    curate_balanced_human_label_templates,
    load_human_gold_cases,
)
from internship_monitor.evaluation.human import _has_student_evidence
from internship_monitor.models import JobListing

PROJECT_ROOT = Path(__file__).parents[1]


def _listing(index: int, title: str, description: str, location: str = "London, UK") -> JobListing:
    return JobListing(
        source="fixture",
        source_job_id=str(index),
        company=f"Company {index}",
        title=title,
        description=description,
        apply_url=f"https://example.com/jobs/{index}",
        location=location,
        discovered_at=datetime(2026, 8, 14, 10, tzinfo=UTC),
    )


def _human_record(listing: JobListing) -> dict[str, object]:
    return {
        "schema_version": "human_gold_v1",
        "case_id": f"human:{listing.source_job_id}",
        "source_identity": f"{listing.source}:{listing.source_job_id}",
        "listing": listing.model_dump(mode="json"),
        "expected": {"relevance": "irrelevant", "hard_block": False},
        "human_rationale": "Existing negative control.",
        "labeling_provenance": "human",
    }


class BalancedCurationTests(TestCase):
    def _configuration(self):
        return load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")

    def _population(self) -> tuple[JobListing, ...]:
        students = tuple(
            _listing(
                index,
                f"Software Engineering Intern {index}",
                "Current university student internship building Python APIs.",
            )
            for index in range(20, 35)
        )
        borderline = tuple(
            _listing(
                index,
                f"Platform Analyst {index}",
                "Early career fixed-term technical analyst for data and developer tooling.",
            )
            for index in range(35, 45)
        )
        edges = tuple(
            _listing(
                index,
                f"Consulting Internship Edge {index}",
                "Student internship. Visa sponsorship support is available.",
                "Singapore; Bengaluru, India" if index % 2 else "Remote EMEA",
            )
            for index in range(45, 50)
        )
        return (*students, *borderline, *edges)

    def test_balanced_curation_preserves_human_records_and_replaces_templates(self) -> None:
        population = self._population()
        preserved_listings = tuple(
            _listing(index, f"Senior Sales {index}", "Full-time senior role.")
            for index in range(10)
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, preserve, first, second = (
                root / "source.jsonl",
                root / "preserve.jsonl",
                root / "one.jsonl",
                root / "two.jsonl",
            )
            source.write_text(
                "".join(listing.model_dump_json() + "\n" for listing in population),
                encoding="utf-8",
            )
            human_lines = [
                json.dumps(_human_record(listing), sort_keys=True) + "\n"
                for listing in preserved_listings
            ]
            template_line = (
                json.dumps(
                    {
                        **_human_record(population[0]),
                        "case_id": "template:old",
                        "labeling_provenance": "template",
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            preserve.write_text("".join((*human_lines, template_line)), encoding="utf-8")
            summary = curate_balanced_human_label_templates(
                source, first, preserve, self._configuration(), limit=40, seed=26
            )
            curate_balanced_human_label_templates(
                source, second, preserve, self._configuration(), limit=40, seed=26
            )
            records = load_human_gold_cases(first, allow_templates=True)
            raw_output = first.read_text(encoding="utf-8")
            deterministic_output = raw_output == second.read_text(encoding="utf-8")
            validation_output = StringIO()
            with redirect_stdout(validation_output):
                validation_exit = main(
                    ["validate-human-gold", "--dataset", str(first), "--allow-templates"]
                )

        self.assertEqual(summary.total_cases, 40)
        self.assertEqual(summary.preserved_human_cases, 10)
        self.assertEqual(summary.new_templates, 30)
        self.assertEqual(raw_output[: sum(len(line) for line in human_lines)], "".join(human_lines))
        self.assertTrue(deterministic_output)
        identities = [case.source_identity for case in records]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertTrue(all(case.labeling_provenance.value == "human" for case in records[:10]))
        self.assertTrue(all(case.labeling_provenance.value == "template" for case in records[10:]))
        self.assertNotIn("template:old", [case.case_id for case in records])
        self.assertEqual(validation_exit, 0)
        self.assertIn("10 human, 30 template", validation_output.getvalue())
        self.assertTrue(
            all(case.expected.relevance is HumanLabelState.NOT_LABELED for case in records[10:])
        )
        self.assertTrue(
            all(
                set(case.expected.model_dump().values()) == {"not_labeled"} for case in records[10:]
            )
        )
        self.assertLessEqual(max(count for _, count in summary.company_counts), 1)

    def test_student_evidence_is_high_recall_but_new_grad_alone_is_not_student_evidence(
        self,
    ) -> None:
        self.assertTrue(
            _has_student_evidence(_listing(1, "Intern", "University student placement."))
        )
        self.assertTrue(_has_student_evidence(_listing(2, "Working Student", "Build APIs.")))
        self.assertFalse(
            _has_student_evidence(_listing(3, "New Grad Engineer", "Early career role."))
        )

    def test_shortfalls_are_reported_without_filling_with_obvious_negatives(self) -> None:
        population = (
            _listing(1, "Intern", "Current student internship."),
            _listing(2, "New Grad Engineer", "Full-time early career role."),
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, preserve, output = (
                root / "source.jsonl",
                root / "preserve.jsonl",
                root / "out.jsonl",
            )
            source.write_text(
                "".join(item.model_dump_json() + "\n" for item in population), encoding="utf-8"
            )
            preserve.write_text("", encoding="utf-8")
            # A preserve file must be non-empty and valid; retain one human control.
            preserve.write_text(
                json.dumps(_human_record(_listing(99, "Control", "Full-time senior role."))) + "\n",
                encoding="utf-8",
            )
            summary = curate_balanced_human_label_templates(
                source, output, preserve, self._configuration(), limit=6, seed=26
            )
        self.assertLess(summary.total_cases, 6)
        self.assertTrue(any(bucket == "plausible_student" for bucket, _, _ in summary.shortfalls))
        self.assertFalse(any("New Grad" in company for company, _ in summary.company_counts))
