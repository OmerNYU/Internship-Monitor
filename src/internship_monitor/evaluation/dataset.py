"""JSONL loading for versioned human-labeled evaluation datasets."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from internship_monitor.evaluation.models import GoldCase


class GoldDatasetError(ValueError):
    """A gold dataset could not be safely read or validated."""


def _validation_summary(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
        for issue in error.errors(include_input=False)
    )


def load_gold_cases(path: str | Path) -> tuple[GoldCase, ...]:
    """Load non-empty version-1 JSONL records with unique stable case identifiers."""
    dataset_path = Path(path)
    try:
        lines = dataset_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise GoldDatasetError(f"could not read gold dataset: {dataset_path}") from error

    cases: list[GoldCase] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise GoldDatasetError(
                f"invalid JSON in gold dataset at line {line_number}: {error.msg}"
            ) from error
        try:
            case = GoldCase.model_validate(payload)
        except ValidationError as error:
            raise GoldDatasetError(
                f"invalid gold dataset record at line {line_number}: {_validation_summary(error)}"
            ) from error
        if case.case_id in seen_ids:
            raise GoldDatasetError(f"duplicate gold case_id at line {line_number}: {case.case_id}")
        seen_ids.add(case.case_id)
        cases.append(case)

    if not cases:
        raise GoldDatasetError(f"gold dataset is empty: {dataset_path}")
    return tuple(cases)
