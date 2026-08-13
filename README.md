# Internship Monitor

Internship Monitor discovers internship listings, assesses their relevance and likely
eligibility, groups matching opportunities, and safely queues explainable alerts. Its current
search focus is Summer 2027 engineering, AI, data, technical product, and consulting roles,
with EMEA and APAC as the primary geographic focus. Regional preference improves relevance; it
never substitutes for evidence about work authorisation or language requirements.

## Project status

Sessions 1 through 24 are implemented. The project has a canonical job model, strict public/private
configuration, failure-isolated async source adapters, Greenhouse and Lever adapters, deterministic role
and eligibility assessment, country/region preferences, durable listing transitions, grouping,
alert policy, notification queueing with retries and daily-digest support, and structured
operational reporting. Email and WhatsApp delivery exist as local, explicit `deliver` capability;
the GitHub Actions deployment deliberately does **not** invoke delivery yet.

## Local setup

Requirements: Python 3.12 and `uv` (recommended).

```bash
uv python install 3.12
uv sync --extra dev
uv run internship-monitor status
uv run pytest
uv run ruff check .
uv run mypy
```

For a dependency-free status check:

```bash
PYTHONPATH=src python -m internship_monitor status
```

## Local operation and privacy

Public examples live in `config/`. Put your personal profile, notification settings,
credentials, and operational state in ignored locations such as `config.local/`, `.env`, and
`state/`. Never commit contact details, work-authorisation records, provider credentials, or
alert history.

Company sources remain explicit allowlist entries. Use `type: greenhouse` with its public board
token, or `type: lever` with the public Lever site slug from `jobs.lever.co/<site>`; both use the
existing `board_token` field. Lever data is normalized at the adapter boundary before it reaches
any analysis or notification code.

The normal monitor path only discovers, assesses, persists, and optionally queues alerts:

```bash
uv run internship-monitor run \
  --profile config.local/profile.yaml \
  --companies config.local/companies.yaml \
  --queue-notifications
```

`run` never sends external messages. `deliver` is the only command that can use private
notification-provider settings, and `deliver --dry-run` is a read-only preview. `status` is also
read-only: it reports state counts and the latest safe monitor/delivery summaries, and clearly
reports uninitialised paths without creating databases.

## GitHub Actions observation rollout

The private repository contains one observation-only workflow at
`.github/workflows/monitor.yml`. It runs every 30 minutes in the `Asia/Karachi` schedule
timezone, supports manual dispatch, and serialises all production state users. It invokes only
`internship-monitor run --queue-notifications` and `internship-monitor status`; it does not
invoke `deliver` and does not require email or WhatsApp secrets.

Before enabling the workflow, set these multiline GitHub Actions secrets from your private YAML
files:

- `INTERNSHIP_MONITOR_PROFILE_YAML`
- `INTERNSHIP_MONITOR_COMPANIES_YAML`

Operational state is a private, unencrypted GitHub Actions artifact named
`internship-monitor-state`, retained for 90 days. It contains `state/jobs.sqlite3`,
`state/notifications.sqlite3`, and `state/manifest.json`. The manifest has a format version and
SHA-256 checksums; restore validates both checksums and SQLite integrity before discovery starts.
Artifacts, rather than cache or Git history, are the authoritative V1 state store.

The first run must be a deliberate manual dispatch with `initialize_state=true`. Ordinary
manual and scheduled runs fail safely when the newest usable state artifact is missing, expired,
unavailable, or corrupt. To recover from a permanently lost artifact, inspect the reason, then
use that same deliberate bootstrap input; this starts fresh listing/notification history.

## Architecture boundaries

```text
source adapters -> canonical listings -> deterministic analysis -> alert decisions -> queued notifications -> explicit delivery
```

Source adapters do not depend on analysis, scoring, persistence, or notification providers.
Operational status is derived from typed, persisted summary models rather than CLI text.

See [`docs/architecture.md`](docs/architecture.md),
[`docs/decisions/001-emea-apac-priority.md`](docs/decisions/001-emea-apac-priority.md),
[`docs/evaluation_harness_v0.1.md`](docs/evaluation_harness_v0.1.md), and
[`docs/intelligence_provider_v0.1.md`](docs/intelligence_provider_v0.1.md) for architecture,
regional, evaluation, and optional-local-provider guidance.
