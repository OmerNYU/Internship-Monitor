# Session 29.2 scale efficiency

Monitoring joins use the durable in-memory listing identity `(source, company, source_job_id)`, not Pydantic object equality. `AlertIndexes` is built once per run and supplies assessment and observation lookup for each opportunity. This changes alert construction from repeated whole-run scans to O(listings + opportunities).

Opportunity grouping retains its conservative matching policy and first-match order. A company/title/location pre-index and per-provider eligibility cache avoid repeated scans of groups which can never match because they already contain the provider.

`deterministic_assessment_cache` is local SQLite state. Its key is the durable identity plus SHA-256 listing fingerprint, SHA-256 profile/policy fingerprint, and `deterministic-assessment-v1`. It contains serialized deterministic assessment fields only; canonical job descriptions are not copied into the cache. Changed analyzer input, profile configuration, or contract version recomputes. Malformed rows fail closed to recomputation. Entries older than 180 days are pruned when a cache batch is persisted.

Authoritative source snapshots and health are persisted before alert decisions, as before. An interruption in alert calculation can leave a completed source/listing observation without a monitor summary, but cannot corrupt the per-source transaction or queue notifications. A later run safely reconciles state.

CLI monitoring emits phase-completion progress to stderr. Shadow remains explicitly opt-in and runs only after deterministic monitoring returns.
