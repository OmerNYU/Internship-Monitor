# Operational runbook v0.1

## Safety boundaries

Normal monitoring and GitHub Actions never send externally. Delivery is an explicit local
command. The optional local intelligence stack is also explicit: deterministic monitoring
works without Ollama.

Secrets remain in environment variables. Do not put SMTP passwords, API tokens, or private
configuration in source control, state artifacts, logs, or review exports.

## Deterministic monitoring

    uv run internship-monitor preflight \
      --profile config.local/profile.yaml \
      --companies config.local/companies.yaml \
      --state state/jobs.sqlite3 \
      --notification-state state/notifications.sqlite3

    uv run internship-monitor run \
      --profile config.local/profile.yaml \
      --companies config.local/companies.yaml \
      --state state/jobs.sqlite3

To queue deterministic alert decisions without sending them, add
`--queue-notifications`. Inspect queue/digest state with:

    uv run internship-monitor status
    uv run internship-monitor digest-preview
    uv run internship-monitor digest-compose

Repeated `digest-compose` creates at most one persisted digest per PKT day.

## Local shadow intelligence

Start Ollama only when explicitly running shadow intelligence. Check it without inference:

    uv run internship-monitor intelligence-status \
      --profile config.local/profile.yaml

Use a bounded local run:

    uv run internship-monitor run \
      --profile config.local/profile.yaml \
      --companies config.local/companies.yaml \
      --shadow-intelligence --shadow-limit 3

If Ollama or a required model is unavailable, the shadow batch is recorded/skipped safely;
the deterministic monitor still completes. Review only semantically useful candidates:

    uv run internship-monitor intelligence-review-candidates \
      --state state/jobs.sqlite3 --limit 25

After an interrupted shadow run, retained observations remain audit data; inspect
`status` for lifecycle/health, restore Ollama, then rerun the explicitly requested shadow
command. Infrastructure-only failures do not suppress a healthy retry.

## Email readiness and controlled test

Create ignored `config.local/notifications.yaml` with email enabled, sender, recipient,
and a separate `email.test_recipient`. Keep the password only in the environment variable
named by `email.password_env_var`.

Validate without SMTP authentication or sending:

    uv run internship-monitor preflight \
      --profile config.local/profile.yaml \
      --companies config.local/companies.yaml \
      --notifications config.local/notifications.yaml \
      --delivery

Preview the exact TEST-only message without writing state:

    uv run internship-monitor deliver-test --dry-run

After a human explicitly approves one test send, run:

    uv run internship-monitor deliver-test \
      --notifications config.local/notifications.yaml \
      --notification-state state/notifications.sqlite3

This command sends only the deterministic `delivery-test:email:v1` row through email to
`email.test_recipient`; it cannot claim unrelated queued alerts or digest work. Rerunning
after success does not send another email. Normal delivery remains separate:

    uv run internship-monitor deliver --dry-run
    uv run internship-monitor deliver --notifications config.local/notifications.yaml

## Claims, retries, and artifacts

A claim expires after five minutes. A stopped worker's unexpired claim is protected; after
expiry, the same channel can reclaim it. Check `status` for claimed or recoverable expired
work. Do not delete delivery rows to recover them.

Artifacts contain only `jobs.sqlite3`, `notifications.sqlite3`, and their checksum
manifest. To validate a restored artifact:

    python -m internship_monitor.deployment.state_bundle validate --state-dir state

A bad or missing artifact must not replace the last good one. GitHub Actions restores,
validates, monitors, composes, manifests, and uploads only after success; it never invokes
Ollama or external delivery.

## Session 28.5b evidence

A user-performed controlled SMTP test passed after delivery preflight: one TEST email
arrived at the configured test recipient, and the immediate identical command recorded no
second external send. The durable test row is delivered with one email-channel attempt.
No credentials, recipient addresses, or secret values are recorded here.

Repeated deterministic cycles also demonstrated stable reconciliation: the second immediate
cycle observed all live listings as unchanged. Queue-only cycles added no candidate rows
when there were no new alert events; the controlled TEST row remained the sole delivered
queue record. Digest preview was empty, so no real digest was composed or delivered.
