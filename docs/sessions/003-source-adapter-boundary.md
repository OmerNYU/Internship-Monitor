# Session 3 - Source adapter boundary

**Status:** Complete
**Date:** 2026-08-12

## What happened

This session added the shared contract that every job-board connection will follow. An adapter
now receives one approved company configuration and returns the same canonical job-listing format,
regardless of whether the provider is Greenhouse, Lever, Ashby, or another supported source.

The runner starts adapters independently. If one source breaks, its failure is recorded with a safe
generic message and the other sources still finish. The returned results stay in the same order as
the configured companies, which makes later summaries predictable.

## What has been achieved so far

The project now has a private-safe foundation, editable profile and company configuration,
a validated canonical job model, and an async adapter boundary with failure isolation. It is ready
for Session 4 to add the first real Greenhouse adapter without coupling source-specific parsing to
later relevance, eligibility, persistence, or notification work.

## Intentionally deferred

- HTTP requests and Greenhouse response parsing.
- Role, location, graduation, and authorization analysis.
- Scoring, persistence, notifications, scheduling, and deployment.

## Verification

```text
ruff check .
ruff format --check .
mypy
pytest -q
internship-monitor status
```

## Next session

Session 4 will implement a Greenhouse adapter against this async contract, using controlled
fixtures and mocked HTTP rather than live network calls in automated tests.
