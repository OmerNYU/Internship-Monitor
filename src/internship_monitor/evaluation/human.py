"""Independent human-gold loading, comparison, and label-free curation."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from internship_monitor.analysis import (
    AssessmentProvider,
    DeterministicAssessor,
    JobAssessment,
    RoleMatchLevel,
)
from internship_monitor.config import SearchConfiguration
from internship_monitor.evaluation.dataset import GoldDatasetError, _validation_summary
from internship_monitor.evaluation.models import (
    HumanGoldCase,
    HumanGoldLabels,
    HumanLabelState,
    HumanRelevance,
    LabelingProvenance,
)
from internship_monitor.models import JobListing


@dataclass(frozen=True, slots=True)
class HumanFieldMismatch:
    field: str
    expected: str | bool
    actual: str | bool


@dataclass(frozen=True, slots=True)
class HumanCaseEvaluation:
    case_id: str
    mismatches: tuple[HumanFieldMismatch, ...]
    compared_fields: int
    expected_retained_incorrectly_blocked: bool
    stage_count: int


@dataclass(frozen=True, slots=True)
class BalancedCurationSummary:
    """Auditable result of replacing only unreviewed pilot templates."""

    preserved_human_cases: int
    new_templates: int
    bucket_counts: tuple[tuple[str, int], ...]
    shortfalls: tuple[tuple[str, int, int], ...]
    company_counts: tuple[tuple[str, int], ...]

    @property
    def total_cases(self) -> int:
        return self.preserved_human_cases + self.new_templates


@dataclass(frozen=True, slots=True)
class HumanEvaluationReport:
    provider_name: str
    cases: tuple[HumanCaseEvaluation, ...]
    compared_fields: int
    mismatch_count: int
    expected_retained_incorrectly_blocked: int
    stage_statuses: tuple[tuple[str, str, int], ...]


def load_human_gold_cases(
    path: str | Path, *, allow_templates: bool = False
) -> tuple[HumanGoldCase, ...]:
    """Load strict independently-human-labeled JSONL cases."""
    dataset_path = Path(path)
    try:
        lines = dataset_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise GoldDatasetError(f"could not read human-gold dataset: {dataset_path}") from error
    cases: list[HumanGoldCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            case = HumanGoldCase.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as error:
            detail = (
                error.msg if isinstance(error, json.JSONDecodeError) else _validation_summary(error)
            )
            raise GoldDatasetError(
                f"invalid human-gold record at line {line_number}: {detail}"
            ) from error
        if case.labeling_provenance is LabelingProvenance.TEMPLATE and not allow_templates:
            raise GoldDatasetError(
                f"human-gold record at line {line_number} is an unreviewed curation template"
            )
        if case.case_id in seen:
            raise GoldDatasetError(
                f"duplicate human-gold case_id at line {line_number}: {case.case_id}"
            )
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise GoldDatasetError(f"human-gold dataset is empty: {dataset_path}")
    return tuple(cases)


def evaluate_human_gold_cases(
    cases: tuple[HumanGoldCase, ...], provider: AssessmentProvider
) -> HumanEvaluationReport:
    """Compare only dimensions explicitly labeled by a human, never inventing labels."""
    outcomes: list[HumanCaseEvaluation] = []
    status_counts: dict[tuple[str, str], int] = {}
    for case in cases:
        assessment = provider.assess(case.listing)
        mismatches = _mismatches(case.expected, assessment)
        for stage in assessment.intelligence_trace.stages:
            key = (stage.stage, stage.status.value)
            status_counts[key] = status_counts.get(key, 0) + 1
        retained = case.expected.hard_block is False
        outcomes.append(
            HumanCaseEvaluation(
                case.case_id,
                mismatches,
                _labeled_field_count(case.expected),
                retained and assessment.is_hard_blocked,
                len(assessment.intelligence_trace.stages),
            )
        )
    return HumanEvaluationReport(
        provider_name=provider.name,
        cases=tuple(outcomes),
        compared_fields=sum(item.compared_fields for item in outcomes),
        mismatch_count=sum(len(item.mismatches) for item in outcomes),
        expected_retained_incorrectly_blocked=sum(
            item.expected_retained_incorrectly_blocked for item in outcomes
        ),
        stage_statuses=tuple(
            (stage, status, count) for (stage, status), count in sorted(status_counts.items())
        ),
    )


def human_report_as_dict(report: HumanEvaluationReport) -> dict[str, object]:
    return asdict(report)


def format_human_evaluation_report(report: HumanEvaluationReport) -> str:
    stages = ", ".join(
        f"{stage}:{status}={count}" for stage, status, count in report.stage_statuses
    )
    return (
        f"Human-gold evaluation: provider={report.provider_name}, cases={len(report.cases)}, "
        f"compared_fields={report.compared_fields}, mismatches={report.mismatch_count}.\n"
        f"Retained cases incorrectly blocked: {report.expected_retained_incorrectly_blocked}.\n"
        f"Provider stages: {stages or 'none'}."
    )


def curate_human_label_templates(
    source: Path,
    output: Path,
    configuration: SearchConfiguration,
    *,
    limit: int,
    seed: int,
) -> int:
    """Create reproducible blank human-label templates; no expected values are inferred."""
    if not 1 <= limit <= 200:
        raise GoldDatasetError("curation limit must be between 1 and 200")
    candidates = _load_listing_candidates(source)
    assessor = DeterministicAssessor(configuration)
    ranked = sorted(
        candidates,
        key=lambda listing: (_stratum(listing, assessor.assess(listing)), _seed_key(listing, seed)),
    )
    selected = _round_robin_by_stratum(ranked, assessor, limit)
    output.parent.mkdir(parents=True, exist_ok=True)
    records = [
        HumanGoldCase(
            case_id=f"curated:{listing.source}:{listing.source_job_id}",
            source_identity=f"{listing.source}:{listing.source_job_id}",
            listing=listing,
            expected=HumanGoldLabels(),
            human_rationale=(
                "Label independently from the listing evidence; do not copy monitor output."
            ),
            labeling_provenance=LabelingProvenance.TEMPLATE,
        ).model_dump(mode="json")
        for listing in selected
    ]
    output.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8"
    )
    return len(records)


def curate_balanced_human_label_templates(
    source: Path,
    output: Path,
    preserve: Path,
    configuration: SearchConfiguration,
    *,
    limit: int,
    seed: int,
) -> BalancedCurationSummary:
    """Preserve reviewed cases and replace templates with a high-recall balanced sample."""
    if not 1 <= limit <= 200:
        raise GoldDatasetError("curation limit must be between 1 and 200")
    preserved = _preserved_human_lines(preserve)
    if len(preserved) > limit:
        raise GoldDatasetError("preserved human cases exceed the requested curation limit")
    candidates = _deduplicated_candidates(_load_listing_candidates(source))
    assessor = DeterministicAssessor(configuration)
    preserved_ids = {case.source_identity for _, case in preserved}
    pool = [listing for listing in candidates if _source_identity(listing) not in preserved_ids]
    assessments = {listing: assessor.assess(listing) for listing in pool}
    slots = limit - len(preserved)
    requested = {
        "plausible_student": min(15, slots),
        "borderline": min(10, max(slots - 15, 0)),
        "geo_auth_language_edge": min(5, max(slots - 25, 0)),
    }
    selected: list[tuple[str, JobListing]] = []
    used_identities = set(preserved_ids)
    used_title_families: set[str] = set()
    for bucket, predicate in (
        ("geo_auth_language_edge", _is_edge_case),
        ("plausible_student", lambda listing, assessment: _has_student_evidence(listing)),
        ("borderline", _is_borderline),
    ):
        count = requested[bucket]
        chosen = _diverse_select(
            [
                listing
                for listing in pool
                if _source_identity(listing) not in used_identities
                and predicate(listing, assessments[listing])
            ],
            count,
            seed,
            used_title_families,
        )
        selected.extend((bucket, listing) for listing in chosen)
        used_identities.update(_source_identity(listing) for listing in chosen)
    # Fill only from still-informative candidates; never backfill with obvious full-time negatives.
    remaining = slots - len(selected)
    if remaining:
        chosen = _diverse_select(
            [
                listing
                for listing in pool
                if _source_identity(listing) not in used_identities
                and (
                    _has_student_evidence(listing)
                    or _is_borderline(listing, assessments[listing])
                    or _is_edge_case(listing, assessments[listing])
                )
            ],
            remaining,
            seed,
            used_title_families,
        )
        selected.extend(("informative_overflow", listing) for listing in chosen)
        used_identities.update(_source_identity(listing) for listing in chosen)
    output.parent.mkdir(parents=True, exist_ok=True)
    generated = [_template_record(listing) for _, listing in selected]
    output.write_text(
        "".join(line for line, _ in preserved)
        + "".join(json.dumps(record, sort_keys=True) + "\n" for record in generated),
        encoding="utf-8",
    )
    selected_counts = Counter(bucket for bucket, _ in selected)
    bucket_counts = (
        ("negative_controls", len(preserved)),
        ("plausible_student", selected_counts["plausible_student"]),
        ("borderline", selected_counts["borderline"]),
        ("geo_auth_language_edge", selected_counts["geo_auth_language_edge"]),
        ("informative_overflow", selected_counts["informative_overflow"]),
    )
    shortfalls = tuple(
        (bucket, requested[bucket], selected_counts[bucket])
        for bucket in ("plausible_student", "borderline", "geo_auth_language_edge")
        if selected_counts[bucket] < requested[bucket]
    )
    companies = Counter(listing.company for _, listing in selected)
    return BalancedCurationSummary(
        preserved_human_cases=len(preserved),
        new_templates=len(generated),
        bucket_counts=bucket_counts,
        shortfalls=shortfalls,
        company_counts=tuple(sorted(companies.items(), key=lambda item: (-item[1], item[0]))),
    )


def _preserved_human_lines(path: Path) -> tuple[tuple[str, HumanGoldCase], ...]:
    """Retain original JSONL lines for human-reviewed records without rewriting them."""
    cases = load_human_gold_cases(path, allow_templates=True)
    raw_by_id: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as error:
        raise GoldDatasetError(f"could not read human-gold dataset: {path}") from error
    for line in lines:
        if line.strip():
            payload = json.loads(line)
            raw_by_id[payload["case_id"]] = line if line.endswith("\n") else f"{line}\n"
    return tuple(
        (raw_by_id[case.case_id], case)
        for case in cases
        if case.labeling_provenance in {LabelingProvenance.HUMAN, LabelingProvenance.HUMAN_REVIEWED}
    )


def _template_record(listing: JobListing) -> dict[str, object]:
    return HumanGoldCase(
        case_id=f"curated:{listing.source}:{listing.source_job_id}",
        source_identity=_source_identity(listing),
        listing=listing,
        expected=HumanGoldLabels(),
        human_rationale=(
            "Label independently from the listing evidence; do not copy monitor output."
        ),
        labeling_provenance=LabelingProvenance.TEMPLATE,
    ).model_dump(mode="json")


def _source_identity(listing: JobListing) -> str:
    return f"{listing.source}:{listing.source_job_id}"


def _deduplicated_candidates(candidates: tuple[JobListing, ...]) -> tuple[JobListing, ...]:
    unique: dict[str, JobListing] = {}
    for listing in candidates:
        unique.setdefault(_source_identity(listing), listing)
    return tuple(unique.values())


def _has_student_evidence(listing: JobListing) -> bool:
    text = f"{listing.title} {listing.description}".casefold()
    return any(
        signal in text
        for signal in (
            "intern",
            "student",
            "summer program",
            "off-cycle",
            "working student",
            "placement",
            "co-op",
            "university",
            "college student",
            "current student",
            "enrolled",
            "returning to school",
            "penultimate year",
            "pre-graduation",
        )
    )


def _is_borderline(listing: JobListing, assessment: JobAssessment) -> bool:
    text = f"{listing.title} {listing.description}".casefold()
    technical = any(
        signal in text
        for signal in (
            "engineer",
            "software",
            "platform",
            "data",
            "analyst",
            "consult",
            "product",
            "machine learning",
            "developer",
        )
    )
    senior = any(signal in text for signal in ("senior", "principal", "director", "manager"))
    return (
        technical
        and not senior
        and not _has_student_evidence(listing)
        and (
            assessment.role.level is not RoleMatchLevel.NOT_RELEVANT
            or any(signal in text for signal in ("early career", "fixed-term", "graduate program"))
        )
    )


def _is_edge_case(listing: JobListing, assessment: JobAssessment) -> bool:
    text = f"{listing.location or ''} {listing.description}".casefold()
    return (
        len(assessment.location.candidates) > 1
        or assessment.location.geographic_bucket.value
        in {"stretch_region", "manual_location_review", "international_remote"}
        or assessment.authorization.status.value
        in {"requires_verification", "positive_support_signal", "explicitly_ineligible"}
        or assessment.language.status.value in {"requires_verification", "incompatible"}
        or "remote" in text
        or "sponsor" in text
    )


def _diverse_select(
    candidates: list[JobListing],
    limit: int,
    seed: int,
    used_title_families: set[str],
    *,
    max_per_company: int | None = None,
    company_counts: Counter[str] | None = None,
) -> tuple[JobListing, ...]:
    groups: dict[str, list[JobListing]] = {}
    for listing in sorted(candidates, key=lambda item: _seed_key(item, seed)):
        groups.setdefault(listing.company.casefold(), []).append(listing)
    selected: list[JobListing] = []
    selected_company_counts = company_counts if company_counts is not None else Counter()
    while groups and len(selected) < limit:
        for company in sorted(tuple(groups)):
            if max_per_company is not None and selected_company_counts[company] >= max_per_company:
                del groups[company]
                continue
            picked: JobListing | None = next(
                (
                    candidate
                    for candidate in groups[company]
                    if _title_family(candidate) not in used_title_families
                ),
                None,
            )
            if picked is None:
                picked = groups[company][0]
            groups[company].remove(picked)
            selected.append(picked)
            selected_company_counts[company] += 1
            used_title_families.add(_title_family(picked))
            if not groups[company]:
                del groups[company]
            if len(selected) == limit:
                break
    return tuple(selected)


def _title_family(listing: JobListing) -> str:
    return " ".join(
        word
        for word in listing.title.casefold().replace("-", " ").split()
        if word not in {"intern", "internship", "student", "the", "and"}
    )


def _mismatches(
    labels: HumanGoldLabels, assessment: JobAssessment
) -> tuple[HumanFieldMismatch, ...]:
    checks: list[tuple[str, str | bool, str | bool]] = []
    if not isinstance(labels.relevance, HumanLabelState):
        actual_relevance: HumanRelevance = (
            HumanRelevance.IRRELEVANT
            if assessment.role.level is RoleMatchLevel.NOT_RELEVANT
            else HumanRelevance.MAYBE
            if assessment.role.level is RoleMatchLevel.REVIEW
            else HumanRelevance.RELEVANT
        )
        checks.append(("relevance", labels.relevance.value, actual_relevance.value))
    direct: tuple[tuple[str, object, object], ...] = (
        ("hard_block", labels.hard_block, assessment.is_hard_blocked),
        ("geographic_bucket", labels.geographic_bucket, assessment.location.geographic_bucket),
        ("strength", labels.strength, assessment.strength),
        ("authorization", labels.authorization, assessment.authorization.status),
        ("language", labels.language, assessment.language.status),
        ("season", labels.season, assessment.season.status),
        ("graduation", labels.graduation, assessment.graduation.status),
    )
    for field, expected, actual in direct:
        if not isinstance(expected, HumanLabelState):
            checks.append((field, _value(expected), _value(actual)))
    if not isinstance(labels.blocker_reason, HumanLabelState):
        actual_blocker = labels.blocker_reason in {item.kind for item in assessment.hard_blockers}
        checks.append(("blocker_reason", labels.blocker_reason.value, actual_blocker))
    return tuple(
        HumanFieldMismatch(field, expected, actual)
        for field, expected, actual in checks
        if expected != actual
    )


def _value(value: object) -> str | bool:
    if isinstance(value, bool):
        return value
    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) else str(raw)


def _labeled_field_count(labels: HumanGoldLabels) -> int:
    values = (
        labels.relevance,
        labels.hard_block,
        labels.blocker_reason,
        labels.role_family,
        labels.geographic_bucket,
        labels.strength,
        labels.authorization,
        labels.language,
        labels.season,
        labels.graduation,
    )
    return sum(not isinstance(value, HumanLabelState) for value in values)


def _load_listing_candidates(path: Path) -> tuple[JobListing, ...]:
    listings: list[JobListing] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            listing = JobListing.model_validate(payload.get("listing", payload))
        except (json.JSONDecodeError, ValidationError) as error:
            raise GoldDatasetError(f"invalid curation input at line {line_number}") from error
        listings.append(listing)
    if not listings:
        raise GoldDatasetError("curation input is empty")
    return tuple(listings)


def _stratum(listing: JobListing, assessment: JobAssessment) -> str:
    text = f"{listing.title} {listing.description} {listing.location or ''}".casefold()
    if assessment.location.geographic_bucket.value == "manual_location_review":
        return "ambiguous_geography"
    if assessment.location.geographic_bucket.value == "stretch_region":
        return "stretch_region"
    if ";" in (listing.location or "") or " and " in (listing.location or "").casefold():
        return "multi_location"
    if "remote" in text:
        return "remote_ambiguity"
    if assessment.authorization.status.value in {"requires_verification", "unknown"}:
        return "authorization_ambiguity"
    if assessment.language.status.value == "requires_verification":
        return "language_ambiguity"
    if assessment.role.level is RoleMatchLevel.REVIEW:
        return "adjacent_or_weird"
    for name, terms in (
        ("ml_ai", ("machine learning", "artificial intelligence", "ml ", " ai ")),
        ("infrastructure_platform", ("platform", "infrastructure", "devops", "cloud")),
        ("data", ("data", "analytics")),
        ("product", ("product",)),
        ("consulting", ("consult",)),
        ("software_backend", ("software", "backend", "python", "api")),
    ):
        if any(term in text for term in terms):
            return name
    if assessment.score < 60 and assessment.role.level is not RoleMatchLevel.NOT_RELEVANT:
        return "low_scoring_plausible"
    return "other"


def _seed_key(listing: JobListing, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{listing.source}:{listing.source_job_id}".encode()).hexdigest()


def _round_robin_by_stratum(
    ranked: list[JobListing], assessor: DeterministicAssessor, limit: int
) -> tuple[JobListing, ...]:
    groups: dict[str, list[JobListing]] = {}
    for listing in ranked:
        groups.setdefault(_stratum(listing, assessor.assess(listing)), []).append(listing)
    selected: list[JobListing] = []
    while groups and len(selected) < limit:
        for name in sorted(tuple(groups)):
            selected.append(groups[name].pop(0))
            if not groups[name]:
                del groups[name]
            if len(selected) == limit:
                break
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class PositiveEnrichedCurationSummary:
    """Auditable selection summary for a template-only positive internship dataset."""

    total_cases: int
    explicit_internship_count: int
    bucket_counts: tuple[tuple[str, int], ...]
    season_evidence_counts: tuple[tuple[str, int], ...]
    geography_counts: tuple[tuple[str, int], ...]
    ambiguous_title_count: int
    negative_control_count: int
    shortfalls: tuple[tuple[str, int, int], ...]
    company_counts: tuple[tuple[str, int], ...]
    overlap_count: int


_POSITIVE_BUCKETS = (
    ("software_backend", 8),
    ("ml_ai", 7),
    ("data", 5),
    ("infrastructure_platform", 4),
    ("product", 4),
    ("consulting", 4),
    ("awkward_valid", 4),
    ("negative_control", 4),
)
_INTERNSHIP_MARKERS = (
    " intern ",
    " internship ",
    " co op ",
    " placement ",
    " working student ",
)
_INTERNSHIP_EMPLOYMENT_TYPES = {"intern", "internship"}
_NEGATIVE_TITLE_MARKERS = (
    "marketing",
    "sales",
    "legal",
    "tax",
    "audit",
    "accounting",
    "human resources",
)
_AMBIGUOUS_TITLE_MARKERS = (
    "applied scientist",
    "research engineer",
    "technical solutions",
    "solutions engineering",
    "solutions engineer",
    "technical analyst",
    "analytics",
    "innovation",
    "emerging technology",
)


def curate_positive_enriched_human_label_templates(
    source: Path,
    output: Path,
    preserve: Path,
    *,
    limit: int,
    seed: int,
) -> PositiveEnrichedCurationSummary:
    """Create a deterministic, internship-only template set without model predictions."""
    if not 1 <= limit <= 200:
        raise GoldDatasetError("curation limit must be between 1 and 200")

    preserved = load_human_gold_cases(preserve, allow_templates=True)
    preserved_ids = {case.source_identity for case in preserved}
    candidates = _deduplicated_candidates(_load_listing_candidates(source))
    pool = [
        listing
        for listing in candidates
        if _source_identity(listing) not in preserved_ids
        and _has_explicit_internship_evidence(listing)
    ]

    selected: list[tuple[str, JobListing]] = []
    used_identities: set[str] = set()
    used_title_families: set[str] = set()
    selected_company_counts: Counter[str] = Counter()
    company_cap = _positive_enriched_company_cap(limit)
    requested = _positive_enriched_requested_buckets(limit)
    for bucket, count in requested:
        chosen = _diverse_select(
            [
                listing
                for listing in pool
                if _source_identity(listing) not in used_identities
                and _positive_enriched_bucket(listing) == bucket
            ],
            count,
            seed,
            used_title_families,
            max_per_company=company_cap,
            company_counts=selected_company_counts,
        )
        selected.extend((bucket, listing) for listing in chosen)
        used_identities.update(_source_identity(listing) for listing in chosen)

    remaining = limit - len(selected)
    if remaining:
        chosen = _diverse_select(
            [
                listing
                for listing in pool
                if _source_identity(listing) not in used_identities
                and _positive_enriched_bucket(listing) is not None
            ],
            remaining,
            seed,
            used_title_families,
            max_per_company=company_cap,
            company_counts=selected_company_counts,
        )
        selected.extend(("informative_overflow", listing) for listing in chosen)
        used_identities.update(_source_identity(listing) for listing in chosen)

    generated = [_template_record(listing) for _, listing in selected]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in generated),
        encoding="utf-8",
    )

    selected_counts = Counter(bucket for bucket, _ in selected)
    bucket_counts = (
        *((bucket, selected_counts[bucket]) for bucket, _ in _POSITIVE_BUCKETS),
        ("informative_overflow", selected_counts["informative_overflow"]),
    )
    bucket_shortfalls = tuple(
        (bucket, count, selected_counts[bucket])
        for bucket, count in requested
        if selected_counts[bucket] < count
    )
    shortfalls = bucket_shortfalls
    if len(selected) < limit:
        shortfalls += (("overall_target", limit, len(selected)),)
    companies = Counter(listing.company for _, listing in selected)
    season_counts = Counter(_curation_season_bucket(listing) for _, listing in selected)
    geography_counts = Counter(_curation_geography_bucket(listing) for _, listing in selected)
    overlap_count = sum(_source_identity(listing) in preserved_ids for _, listing in selected)
    return PositiveEnrichedCurationSummary(
        total_cases=len(selected),
        explicit_internship_count=sum(
            _has_explicit_internship_evidence(listing) for _, listing in selected
        ),
        bucket_counts=bucket_counts,
        season_evidence_counts=tuple(sorted(season_counts.items())),
        geography_counts=tuple(sorted(geography_counts.items())),
        ambiguous_title_count=sum(_has_ambiguous_title(listing) for _, listing in selected),
        negative_control_count=selected_counts["negative_control"],
        shortfalls=shortfalls,
        company_counts=tuple(sorted(companies.items(), key=lambda item: (-item[1], item[0]))),
        overlap_count=overlap_count,
    )


def _positive_enriched_requested_buckets(limit: int) -> tuple[tuple[str, int], ...]:
    remaining = limit
    requested: list[tuple[str, int]] = []
    for bucket, quota in _POSITIVE_BUCKETS:
        count = min(quota, remaining)
        requested.append((bucket, count))
        remaining -= count
    return tuple(requested)


def _positive_enriched_company_cap(limit: int) -> int:
    """Keep a sparse canonical export from being dominated by one employer."""
    return max(3, limit // 5)


def _has_explicit_internship_evidence(listing: JobListing) -> bool:
    """Require a role-level internship signal, not incidental description wording."""
    title = _curation_title(listing)
    employment_type = _normalize_curation_text(listing.employment_type or "")
    return any(marker in title for marker in _INTERNSHIP_MARKERS) or (
        employment_type in _INTERNSHIP_EMPLOYMENT_TYPES
    )


def _positive_enriched_bucket(listing: JobListing) -> str | None:
    title = _curation_title(listing)
    evidence = _curation_text(listing)
    if any(marker in title for marker in _NEGATIVE_TITLE_MARKERS):
        return "negative_control"
    if any(
        marker in evidence
        for marker in (
            "machine learning",
            " ml ",
            " ai ",
            "artificial intelligence",
            "applied ai",
            "applied scientist",
            "research engineer",
            "llm",
            "nlp",
            "computer vision",
            "generative ai",
        )
    ):
        return "ml_ai"
    if any(
        marker in evidence
        for marker in (
            "data science",
            "data scientist",
            "data engineer",
            "data analyst",
            "analytics",
        )
    ):
        return "data"
    if any(
        marker in evidence
        for marker in (
            "platform",
            "infrastructure",
            "cloud",
            "devops",
            "site reliability",
            " sre ",
        )
    ):
        return "infrastructure_platform"
    if any(
        marker in evidence
        for marker in ("software", "backend", "systems", "developer", "engineering")
    ):
        return "software_backend"
    if "product" in evidence:
        return "product"
    if any(marker in evidence for marker in ("consult", "advisory")):
        return "consulting"
    if _has_ambiguous_title(listing):
        return "awkward_valid"
    return None


def _has_ambiguous_title(listing: JobListing) -> bool:
    title = _curation_title(listing)
    return any(marker in title for marker in _AMBIGUOUS_TITLE_MARKERS)


def _curation_text(listing: JobListing) -> str:
    title = _normalize_curation_text(listing.title)
    description = _normalize_curation_text(listing.description)
    return f" {title} {description} "


def _curation_title(listing: JobListing) -> str:
    return f" {_normalize_curation_text(listing.title)} "


def _normalize_curation_text(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _curation_season_bucket(listing: JobListing) -> str:
    text = _curation_text(listing)
    if "winter" in text and ("2026" in text or "2027" in text):
        return "winter_2026_27_evidence"
    if "spring 2027" in text:
        return "spring_2027_evidence"
    if "summer 2027" in text:
        return "summer_2027_evidence"
    if any(term in text for term in ("fall ", "autumn ", "summer 2026", "spring 2026")):
        return "explicit_other_season"
    return "unknown"


def _curation_geography_bucket(listing: JobListing) -> str:
    text = _normalize_curation_text(f"{listing.location or ''} {listing.workplace_type or ''}")
    if not text:
        return "unknown"
    if "remote" in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    return "named_location"
