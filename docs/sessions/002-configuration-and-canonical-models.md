# Session 2 - Configuration and canonical models

**Status:** Complete
**Date:** 2026-08-12

## Objective

Introduce the source-independent job representation and validated, user-controlled configuration
required by the charter without beginning source adapters, analysis, persistence, or notification
work.

## Implemented

- An immutable `JobListing` model containing the charter's canonical fields.
- Absolute HTTP(S) application URL validation and timezone-aware datetime validation.
- Strict profile, role-preference, regional-strategy, remote-policy, authorization, company-source,
  and explicit company-allowlist models.
- Safe YAML loaders whose validation errors identify field paths without echoing secret-bearing
  configuration values.
- Configurable role families, internship periods, technical signals, regions, preferred markets,
  authorization facts, and remote policies.
- EMEA and APAC as separate primary regions in public example data, with preferred markets kept
  separate from authorization facts.

## Boundaries preserved

- No provider-specific field is accepted by the canonical job model.
- Configuration rejects unknown fields instead of silently ignoring misspellings.
- Loading a company file does not discover or add companies.
- Regional preference does not infer legal eligibility.
- The public profile contains fake academic and authorization values.

## Intentionally deferred

- Source adapter protocols and result/failure types (Session 3).
- Greenhouse fetching and normalization.
- Role, location, graduation, and authorization analysis.
- Scoring, persistence, notifications, scheduling, and deployment.

## Verification

```text
ruff check .       -> passed
mypy              -> passed (strict, 12 source files)
pytest -q          -> 13 passed
git diff --check  -> passed
```

## Next session

Session 3 should define the adapter boundary, including an adapter protocol, source identity,
normalized fetch results, per-source failure reporting, and tests for failure isolation. It should
not yet implement Greenhouse network access or classification behavior.
