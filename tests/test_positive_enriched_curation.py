import json
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.cli import main
from internship_monitor.evaluation import (
    HumanLabelState,
    curate_positive_enriched_human_label_templates,
    load_human_gold_cases,
)
from internship_monitor.evaluation.human import _has_explicit_internship_evidence
from internship_monitor.models import JobListing


def _listing(index: int, title: str, description: str, location: str = "London, UK") -> JobListing:
    return JobListing(
        source="positive-fixture",
        source_job_id=str(index),
        company=f"Company {index}",
        title=title,
        description=description,
        apply_url=f"https://example.com/jobs/{index}",
        location=location,
        discovered_at=datetime(2026, 8, 15, 10, tzinfo=UTC),
    )


def _human_record(listing: JobListing) -> dict[str, object]:
    return {
        "schema_version": "human_gold_v1",
        "case_id": f"human:{listing.source_job_id}",
        "source_identity": f"{listing.source}:{listing.source_job_id}",
        "listing": listing.model_dump(mode="json"),
        "expected": {"relevance": "irrelevant", "hard_block": False},
        "human_rationale": "Existing reviewed regression record.",
        "labeling_provenance": "human",
    }


class PositiveEnrichedCurationTests(TestCase):
    def _write_population(self, path: Path, population: tuple[JobListing, ...]) -> None:
        path.write_text(
            "".join(item.model_dump_json() + "\n" for item in population), encoding="utf-8"
        )

    def test_positive_enriched_curation_is_diverse_template_only_and_deterministic(self) -> None:
        preserved = _listing(90, "Software Engineer Intern", "Summer 2027 internship.")
        population = (
            _listing(1, "Software Engineer Intern", "Summer 2027 university internship."),
            _listing(2, "AI Applied Scientist Intern", "Winter 2026/27 AI internship."),
            _listing(3, "Data Science Intern", "Spring 2027 data internship."),
            _listing(4, "Cloud Platform Intern", "Summer 2027 infrastructure internship."),
            _listing(5, "Technical Product Intern", "Summer 2027 product internship."),
            _listing(6, "Technology Consulting Intern", "Spring 2027 consulting internship."),
            _listing(7, "Technical Solutions Intern", "University placement rotation."),
            _listing(8, "Marketing Intern", "Summer 2027 internship."),
            _listing(9, "Research Engineer Intern", "Machine learning internship."),
            _listing(10, "Software Engineer Intern", "Fall 2026 internship."),
            _listing(
                11,
                "Intern",
                "Software backend internship. Work authorization details are not specified.",
                "Berlin, Germany",
            ),
            _listing(
                12,
                "Software Engineering Intern",
                "Internship. Applicants must already be authorized to work in the US.",
            ),
            preserved,
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, preserve, first, second = (
                root / "source.jsonl",
                root / "preserve.jsonl",
                root / "one.jsonl",
                root / "two.jsonl",
            )
            self._write_population(source, population)
            preserve.write_text(json.dumps(_human_record(preserved)) + "\n", encoding="utf-8")
            summary = curate_positive_enriched_human_label_templates(
                source, first, preserve, limit=40, seed=27
            )
            curate_positive_enriched_human_label_templates(
                source, second, preserve, limit=40, seed=27
            )
            records = load_human_gold_cases(first, allow_templates=True)
            first_text = first.read_text(encoding="utf-8")
            second_text = second.read_text(encoding="utf-8")
            validation_output = StringIO()
            with redirect_stdout(validation_output):
                validation_exit = main(
                    ["validate-human-gold", "--dataset", str(first), "--allow-templates"]
                )

        identities = {case.source_identity for case in records}
        bucket_counts = dict(summary.bucket_counts)
        selected_titles = {case.listing.title for case in records}
        self.assertEqual(first_text, second_text)
        self.assertEqual(summary.overlap_count, 0)
        self.assertNotIn("positive-fixture:90", identities)
        self.assertEqual(summary.total_cases, len(records))
        self.assertEqual(summary.explicit_internship_count, len(records))
        self.assertTrue(all(case.labeling_provenance.value == "template" for case in records))
        self.assertTrue(
            all(
                set(case.expected.model_dump().values()) == {"not_labeled"}
                and case.expected.relevance is HumanLabelState.NOT_LABELED
                for case in records
            )
        )
        self.assertEqual(validation_exit, 0)
        self.assertIn("template", validation_output.getvalue())
        self.assertEqual(bucket_counts["software_backend"], 4)
        self.assertEqual(bucket_counts["ml_ai"], 2)
        self.assertEqual(bucket_counts["data"], 1)
        self.assertEqual(bucket_counts["infrastructure_platform"], 1)
        self.assertEqual(bucket_counts["product"], 1)
        self.assertEqual(bucket_counts["consulting"], 1)
        self.assertEqual(bucket_counts["awkward_valid"], 1)
        self.assertEqual(bucket_counts["negative_control"], 1)
        self.assertIn("Marketing Intern", selected_titles)
        self.assertIn("Fall 2026 internship.", {case.listing.description for case in records})
        self.assertIn(
            "Software backend internship. Work authorization details are not specified.",
            {case.listing.description for case in records},
        )
        self.assertIn(
            "Internship. Applicants must already be authorized to work in the US.",
            {case.listing.description for case in records},
        )
        self.assertEqual(max(count for _, count in summary.company_counts), 1)

    def test_noninternship_roles_are_not_positive_evidence_and_shortfalls_are_honest(self) -> None:
        new_grad = _listing(1, "New Grad Software Engineer", "Full-time early career role.")
        full_time = _listing(2, "Applied Scientist", "Permanent full-time role.")
        unrelated = _listing(3, "Intern", "General student experience with no technical scope.")
        self.assertFalse(_has_explicit_internship_evidence(new_grad))
        self.assertFalse(_has_explicit_internship_evidence(full_time))
        self.assertTrue(_has_explicit_internship_evidence(unrelated))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, preserve, output = (
                root / "source.jsonl",
                root / "preserve.jsonl",
                root / "out.jsonl",
            )
            self._write_population(source, (new_grad, full_time, unrelated))
            preserve.write_text(
                json.dumps(_human_record(_listing(99, "Control", "Reviewed role."))) + "\n",
                encoding="utf-8",
            )
            summary = curate_positive_enriched_human_label_templates(
                source, output, preserve, limit=6, seed=27
            )
            output_text = output.read_text(encoding="utf-8")

        self.assertEqual(summary.total_cases, 0)
        self.assertTrue(summary.shortfalls)
        self.assertEqual(output_text, "")
