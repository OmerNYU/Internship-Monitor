# Internship Monitor

Internship Monitor is a configurable system for discovering internships, evaluating their
relevance, identifying likely eligibility issues, and delivering explainable alerts.

The initial monitor targets Summer 2027 engineering, AI, data, technical product, and
consulting opportunities. EMEA and APAC are the primary geographic focus regions. Regional
preference affects relevance; it never substitutes for evidence about work authorization.

## Project status

Sessions 1 through 4 establish the repository foundation, canonical job model, validated public
configuration, failure-isolated source adapter contract, and Greenhouse source adapter. Analysis,
persistence, notifications, and scheduling are intentionally not implemented yet.

## Requirements

- Python 3.12
- `uv` (recommended package and environment manager)

## Local setup

```bash
uv python install 3.12
uv sync --extra dev
uv run internship-monitor status
uv run pytest
uv run ruff check .
uv run mypy
```

Until `uv` is available, the dependency-free status command can also run with Python directly:

```bash
PYTHONPATH=src python -m internship_monitor status
```

## Configuration and privacy

Public examples live under `config/`. Personal profiles, notification settings, credentials,
and operational state belong under ignored paths such as `config.local/`, `.env`, and `state/`.

Never commit real contact details, authorization records, provider credentials, or alert history.

The regional configuration in [`config/profile.example.yaml`](config/profile.example.yaml) is an
example, not legal guidance. A future user should be able to customize it without changing the
classifier source code.

## Architecture boundaries

The core pipeline will remain source- and notifier-independent:

```text
source adapters -> canonical listings -> deterministic analysis -> alert decisions -> notifiers
```

See [`docs/architecture.md`](docs/architecture.md) for the initial module boundaries and
[`docs/decisions/001-emea-apac-priority.md`](docs/decisions/001-emea-apac-priority.md) for the
approved regional amendment.

The latest implementation record is
[`docs/sessions/004-greenhouse-adapter.md`](docs/sessions/004-greenhouse-adapter.md).

