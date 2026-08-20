# Daily digest v0.1

The daily digest is a deterministic, channel-independent operational summary. It is deliberately separate from discovery, queueing, and external delivery:

monitor run -> queue deterministic candidates -> digest-compose -> explicit deliver

Neither the digest nor its ordering uses shadow-intelligence verdicts, model confidence, RAG results, or provider availability.

## Candidate snapshots and composition

When a deterministic alert decision is queued for the digest, the notification queue stores a small canonical snapshot: company, title, location, role family, score, recommendation, season, authorization, graduation, and apply URL. It never stores prompts, model output, RAG excerpts, exception bodies, or credentials.

The digest-compose command creates at most one logical digest for the current Asia/Karachi date. The key is daily_digest:<PKT-date>; retries always operate on that same persisted digest model. Candidate records remember the digest key that actually included them, so catch-up delivery can complete the correct original candidates.

No candidate or recap means no digest row is created. A delivered digest is not regenerated or resent.

## Timing and catch-up

Composition is eligible at 11:00 PKT. Before then, preview explains that no digest is eligible and composition is a no-op. A late same-day invocation makes the one current-day digest.

Pending candidates scheduled for prior dates are included once in the current digest as catch-up items, retaining their original date in the rendered item. This intentionally creates one bounded catch-up digest rather than an unlimited historical backfill. Candidates queued after 11:00 PKT follow the existing alert policy and are assigned to the next PKT digest.

Immediate alerts are not duplicated as full digest entries. Alerts scheduled before 11:00 appear only in a compact same-cycle recap containing company, title, and score. Alerts arriving after 11:00 are recapped with the following day.

## Rendering and source health

Plain text is the canonical renderer for this release. Items appear as:

1. strong actionable opportunities (apply_immediately, then strong_candidate);
2. manual review;
3. lower-priority / eligibility review.

Within each section items are grouped by location and ordered deterministically. The role family is shown as a compact field.

Composition snapshots safe per-source health from local persisted state: healthy, degraded, and failed counts plus non-healthy company/adapter/category records. It renders typed safe categories only, never raw exceptions.

## Commands and hosted operation

- internship-monitor digest-preview --state … --notification-state … [--json] is read-only. It returns the persisted current digest when present, otherwise a transient eligible digest, or a clear empty/before-11 result.
- internship-monitor digest-compose --state … --notification-state … [--json] writes only the logical digest and candidate lifecycle transitions. It does not load notifier configuration and cannot send email or WhatsApp.
- internship-monitor deliver remains the only external delivery boundary.

The hosted monitor workflow invokes digest-compose after its deterministic queueing run. This is observation-only: it does not send, invoke Ollama, or enable shadow intelligence. The existing state artifact already includes notifications.sqlite3, so persisted digest/retry state is restored and uploaded with the rest of local state.
