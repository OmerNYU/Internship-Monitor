# Delivery leases v0.1

Delivery is explicit (internship-monitor deliver) and remains disabled in GitHub Actions.
The queue record is logical notification content; each provider channel has an independent
SQLite delivery row.

A worker claims a due channel in a BEGIN IMMEDIATE transaction. The claim carries an
opaque UUID token and a five-minute expiry. Completion accepts only the active token.
Expired claims may be reclaimed; delivered channels and terminal failures cannot be claimed.

Each claim creates a secret-free attempt audit row. Retryable failures use the existing
bounded retry/backoff policy. Email and WhatsApp do not share success state: an email
success is final for email even when WhatsApp is retrying.

Queue claiming is exactly-once, not external sending. A provider can accept a message just
before the process crashes and before SQLite records completion; a retry can then duplicate
the external message. SMTP and the currently configured WhatsApp path do not provide a
portable provider-side idempotency contract, so this ambiguity is documented rather than
misrepresented as exactly-once delivery. Digest retries always reuse the persisted digest
payload and logical PKT-day key.


## State artifact lifecycle

The artifact manifest has an explicit two-file allowlist: jobs.sqlite3 and
notifications.sqlite3. Those databases contain listing/source health and shadow provenance,
plus queue/digest/attempt state. The manifest intentionally excludes local configuration,
secrets, RAG material, embedding cache, human-gold data, and raw exports. A workflow
validates checksum and SQLite integrity before observation; upload occurs only after monitor,
digest composition, and manifest creation complete successfully, preserving the prior good
artifact on failure.
