# Source Catalog v0.1 — role-first discovery foundation

## Product boundary

The opportunity universe is no longer conceptually a user's company watchlist. A shared, curated
source catalog supplies safe structured boards; a user profile determines which canonical listings
are relevant and eligible. The flow is:

```text
catalog discovery (candidate only) -> verified structured sources -> canonical listings
-> profile-specific role / season / geography / authorization / graduation / language assessment
-> deterministic ranking -> optional local semantic review -> alerts and digest
```

This does **not** authorize arbitrary crawling. LinkedIn, Indeed, Google Jobs, and aggregator result
pages are out of scope. A monitor request is made only when an entry names a supported provider,
has a provider-valid board identifier, is `verified`, and is explicitly `enabled`.

## Allowlist coupling audit

| Location | Existing coupling | Classification | v0.1 handling |
| --- | --- | --- | --- |
| `config.models.CompanyAllowlist` and `target_regions` | Company list contains both board identity and historical regional preference | Historical product assumption; `target_regions` is a user preference | `SourceCatalogEntry` contains only source facts; regional policy remains in `SearchConfiguration`. |
| `config.loader` | Only loads `companies.yaml` | Historical product assumption | Adds independent `load_source_catalog`. |
| Greenhouse/Lever adapters and adapter registry | Require enabled provider/type/token before a request | Genuine source-safety requirement | Preserved; Ashby follows the same contract. |
| orchestration | Iterates enabled allowlist companies | Historical product assumption | Accepts a catalog and derives only its verified/enabled source set before fetch. |
| state and source health | `(provider, company, source_job_id)` is the current durable identity | Reusable operational identity, but not a catalog identity | Preserved for state compatibility. Catalog `source_id` is stable curation identity; a future state migration can add it without rewriting history. |
| CLI and preflight | `--companies` path and allowlist wording | Historical product assumption plus safety validation | `--catalog` takes precedence; legacy `--companies` remains supported. |
| GitHub Actions | private companies secret is materialized | Historical configuration transport | Existing secret remains valid during migration; catalog secret support can be introduced without changing delivery behavior. |
| tests and README/runbook | company list presented as the opportunity universe | Documentation/test historical assumption | Catalog tests and this document make the separate boundaries explicit. |

## Schema and lifecycle

A catalog is version-controlled YAML initially. Its records have `source_id`, canonical employer
name, provider, provider board ID, careers URL, enabled flag, provenance, verification status,
verification/discovery timestamps, country hints, and metadata version. It intentionally has no
role, degree, graduation, season, language, geography preference, or authorization fields.

```yaml
version: 1
sources:
  - source_id: employer-ashby-board
    canonical_employer_name: Employer
    provider: ashby
    provider_board_id: employer
    careers_url: https://jobs.ashbyhq.com/employer
    enabled: true
    discovery_provenance: manual
    verification_status: verified
    first_discovered_at: 2026-08-22T00:00:00Z
    last_verified_at: 2026-08-22T00:00:00Z
    country_hints: [United Kingdom]
    metadata_version: 1
```

`candidate`, `disabled`, `unhealthy`, and `retired` records never enter normal monitoring.
`unhealthy` is a catalog-review lifecycle state, distinct from the per-run source-health data
already persisted in SQLite. New candidates require identifier validation, supported-adapter
validation, a bounded public endpoint probe, and a curator decision before becoming `verified`.

The smallest scalable persistence shape is a hybrid: a reviewed catalog in version-controlled YAML
for contributor review and reproducibility, and the existing SQLite state only for listing
transitions, per-run health, and scheduling. It scales to hundreds or low thousands of curated
entries without prematurely introducing a service. A future shared catalog service may retain the
same versioned record contract.

## Migration and operation

`SourceCatalog.from_legacy_allowlist` imports an existing approved company file into equivalent
provider/board source records. Enabled legacy Greenhouse, Lever, and Ashby entries become verified
catalog entries; disabled legacy entries become disabled catalog records. Historical
`target_regions` are intentionally not copied because they are not catalog facts.

During migration both commands are valid:

```bash
uv run internship-monitor run --profile config.local/profile.yaml --companies config.local/companies.yaml
uv run internship-monitor run --profile config.local/profile.yaml --catalog config.local/source_catalog.yaml
```

The catalog command fetches the same provider/board source set when the catalog is the legacy
import. `--catalog` also works with `preflight`. Company priority/exclusion is a profile-owned hint
and is not used to build adapters or request sources.

## Role and eligibility first

One fetched canonical listing can be evaluated under many independent profiles. Today the single
`SearchConfiguration` is passed after ingestion; no account system is added. The future shape is:

```text
shared source ingestion -> canonical opportunity store -> profile-specific assessments
```

This preserves visa-first filtering. `AuthorizationAnalyzer` remains independent of role relevance
and only trusts listing text plus the user's existing country support facts. Its statuses remain
`authorized`, `positive_support_signal`, `requires_verification`, `likely_ineligible`,
`explicitly_ineligible`, and `unknown` where applicable in the assessment contract. Company
reputation, catalog country hints, and future RAG context cannot override an explicit listing
restriction or silently infer sponsorship.

## Provider roadmap and scale

Implementation order is Ashby, SmartRecruiters, Recruitee, then Workable/Teamtailor after their
public structured contracts are verified. Workday stays a separate design effort because its
public-facing surfaces and tenant conventions are materially less uniform. Ashby is now supported
through its public job-board endpoint and only normalizes source data; it has no user relevance
logic.

Adapter execution is bounded to 16 concurrent sources, preserving configured result order. At
hundreds of sources, maintain provider/host caps, bounded retries, source health, and incremental
scheduling before increasing polling frequency. Do not assume every board has many jobs.

Catalog discovery is a separate, non-production process: collect candidates from known ATS URLs,
employer careers pages, provider directories, manual submissions, or approved bounded web search;
record `manual`, `provider-directory discovery`, `web discovery`, or `imported curated dataset`
provenance; validate; then explicitly promote. It must never silently promote a discovered source.

## Shadow checkpoint

After a catalog change produces genuinely new source diversity, run a bounded local pass only:

```bash
uv run internship-monitor run \
  --profile config.local/profile.yaml \
  --catalog config.local/source_catalog.yaml \
  --shadow-intelligence --shadow-limit 3
```

Report the resulting semantic review-candidate count from the command. Do not run Ollama in GitHub
Actions or tune AI policy as part of catalog ingestion.
