# Initial architecture

Internship Monitor uses a deterministic pipeline whose reusable core is isolated from source
providers, user-specific data, persistence, and delivery channels.

## Planned package boundaries

```text
internship_monitor/
├── adapters/       # Fetch and normalize source-specific listings
├── analysis/       # Role, location, graduation, and authorization evidence
├── config/         # Typed loading and validation of user-controlled behavior
├── models/         # Canonical listings and neutral alert decisions
├── notifications/  # Email, WhatsApp, and console formatting/delivery
├── persistence/    # Deduplication and operational state
├── scheduling/     # Immediate-alert windows, queues, and digests
└── cli.py          # Composition root and operator commands
```

Folders will be introduced when their implementation session begins; empty architectural shells
are avoided.

## Dependency direction

- Adapters may depend on canonical models, never on scoring or notifiers.
- Analysis may depend on canonical models and validated configuration, never on source details.
- Scheduling consumes neutral alert decisions and does not alter scores.
- Notifiers format and deliver decisions; they do not duplicate filtering logic.
- Personal configuration and secrets remain outside the reusable package.

## Regional behavior

EMEA and APAC are primary search regions. A region match is a relevance signal and can guide
which approved company endpoints are queried. It cannot produce an `authorized` result. Remote,
regional, and country-level constraints remain visible eligibility evidence and may require manual
verification.

