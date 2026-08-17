# ruff: noqa: E501
"""Small SQLite repository for stable job-listing state and safe source health."""

from __future__ import annotations

# ruff: noqa: E501
import hashlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from internship_monitor.models import JobListing
from internship_monitor.reporting.models import ListingStateCounts, MonitorRunSummary
from internship_monitor.state.models import (
    ListingChange,
    ListingObservation,
    SourceHealthRecord,
    SourceHealthStatus,
    SourceHealthSummary,
)

_SOURCE_HEALTH_RETENTION = 300
_RECENT_ISSUE_WINDOW = 20


def _utc_now() -> datetime:
    return datetime.now(UTC)


class JobStateRepository:
    """Persist minimal listing fingerprints and safe source-health observations."""

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        state_path = Path(path)
        self._read_only = read_only
        if read_only and state_path.exists():
            self._connection = sqlite3.connect(f"{state_path.resolve().as_uri()}?mode=ro", uri=True)
            self._connection.row_factory = sqlite3.Row
        elif read_only:
            self._connection = sqlite3.connect(":memory:")
            self._connection.row_factory = sqlite3.Row
            self._create_schema()
        else:
            self._connection = sqlite3.connect(state_path)
            self._connection.row_factory = sqlite3.Row
            self._create_schema()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> JobStateRepository:
        return self

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        self.close()

    def active_listing_count(self, *, source_type: str, company: str) -> int:
        """Return existing active inventory for one stable source identity."""
        row = self._connection.execute(
            """
            SELECT COUNT(*) AS active
            FROM job_state
            WHERE source = ? AND company = ? AND active = 1
            """,
            (source_type, company),
        ).fetchone()
        assert row is not None
        return int(row["active"])

    def compare_successful_source_run(
        self,
        listings: Sequence[JobListing],
        *,
        source_type: str,
        company: str,
    ) -> tuple[ListingObservation, ...]:
        """Compare an authoritative snapshot without changing database state."""
        self._validate_snapshot(listings, source_type, company)
        return tuple(
            ListingObservation(listing=listing, change=self._change_for_listing(listing))
            for listing in listings
        )

    def record_successful_source_run(
        self,
        listings: Sequence[JobListing],
        *,
        source_type: str,
        company: str,
        observed_at: datetime | None = None,
    ) -> tuple[ListingObservation, ...]:
        """Record an authoritative source snapshot and return transitions in input order.

        Callers must not call this method for failed or non-authoritative snapshots.
        """
        if self._read_only:
            raise RuntimeError("cannot record listing state through a read-only repository")
        timestamp = observed_at or _utc_now()
        _require_aware(timestamp)
        self._validate_snapshot(listings, source_type, company)
        observed_keys = {_listing_key(listing) for listing in listings}
        with self._connection:
            observations = tuple(
                ListingObservation(listing=listing, change=self._record_listing(listing, timestamp))
                for listing in listings
            )
            self._mark_unseen_listings_inactive(source_type, company, observed_keys)
        return observations

    def record_source_health(self, record: SourceHealthRecord) -> None:
        """Persist safe source health with bounded per-source history."""
        if self._read_only:
            raise RuntimeError("cannot record source health through a read-only repository")
        _require_aware(record.observed_at)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO source_run_health (
                    source_type, company, observed_at, status, authoritative,
                    listing_count, previous_active_count, attempt_count, duration_ms,
                    failure_category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.source_type,
                    record.company,
                    _timestamp_text(record.observed_at),
                    record.status.value,
                    int(record.authoritative),
                    record.listing_count,
                    record.previous_active_count,
                    record.attempt_count,
                    record.duration_ms,
                    record.failure_category,
                ),
            )
            self._connection.execute(
                """
                DELETE FROM source_run_health
                WHERE id NOT IN (
                    SELECT id FROM source_run_health
                    WHERE source_type = ? AND company = ?
                    ORDER BY id DESC LIMIT ?
                ) AND source_type = ? AND company = ?
                """,
                (
                    record.source_type,
                    record.company,
                    _SOURCE_HEALTH_RETENTION,
                    record.source_type,
                    record.company,
                ),
            )

    def source_health_summaries(self) -> tuple[SourceHealthSummary, ...]:
        """Return latest safe health per source with a small recent issue count."""
        try:
            rows = self._connection.execute(
                """
                SELECT latest.source_type, latest.company, latest.observed_at, latest.status,
                       latest.authoritative, latest.listing_count, latest.previous_active_count,
                       latest.attempt_count, latest.duration_ms, latest.failure_category,
                       (
                           SELECT MAX(authoritative_run.observed_at)
                           FROM source_run_health AS authoritative_run
                           WHERE authoritative_run.source_type = latest.source_type
                             AND authoritative_run.company = latest.company
                             AND authoritative_run.authoritative = 1
                             AND authoritative_run.status = 'healthy'
                       ) AS last_authoritative_success_at,
                       (
                           SELECT COUNT(*)
                           FROM (
                               SELECT status FROM source_run_health AS recent
                               WHERE recent.source_type = latest.source_type
                                 AND recent.company = latest.company
                               ORDER BY recent.id DESC LIMIT ?
                           ) WHERE status != 'healthy'
                       ) AS recent_issue_count
                FROM source_run_health AS latest
                WHERE latest.id = (
                    SELECT source_latest.id FROM source_run_health AS source_latest
                    WHERE source_latest.source_type = latest.source_type
                      AND source_latest.company = latest.company
                    ORDER BY source_latest.id DESC LIMIT 1
                )
                ORDER BY latest.company COLLATE NOCASE, latest.source_type
                """,
                (_RECENT_ISSUE_WINDOW,),
            ).fetchall()
        except sqlite3.OperationalError:
            return ()
        return tuple(_source_health_summary_from_row(row) for row in rows)

    def shadow_assessment_exists(
        self,
        listing: JobListing,
        semantic_fingerprint: str,
        deterministic_fingerprint: str,
        contract_fingerprint: str,
    ) -> bool:
        try:
            row = self._connection.execute(
                """SELECT 1 FROM shadow_assessments WHERE source = ? AND company = ?
                AND source_job_id = ? AND semantic_fingerprint = ?
                AND deterministic_fingerprint = ? AND contract_fingerprint = ? LIMIT 1""",
                (
                    listing.source,
                    listing.company,
                    listing.source_job_id,
                    semantic_fingerprint,
                    deterministic_fingerprint,
                    contract_fingerprint,
                ),
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None

    def record_shadow_assessment(self, record: Any, retention_days: int) -> None:
        """Append a safe local shadow observation and bounded stage provenance."""
        if self._read_only:
            raise RuntimeError("cannot record shadow assessment through a read-only repository")
        listing = record.listing
        with self._connection:
            cursor = self._connection.execute(
                """INSERT INTO shadow_assessments (
                source, company, source_job_id, observed_at, semantic_fingerprint, deterministic_fingerprint,
                contract_fingerprint, rag_fingerprint, routing_category, status, title, description, location,
                workplace_type, employment_type, apply_url, deterministic_role_level, deterministic_role_family,
                hard_blocked, blocker_categories, recommendation, proposed_role_level, proposed_role_family,
                confidence, evidence_grounded, citation_grounded, failure_category, fallback_reason,
                policy_rejection, elapsed_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    listing.source,
                    listing.company,
                    listing.source_job_id,
                    _timestamp_text(record.observed_at),
                    record.semantic_fingerprint,
                    record.deterministic_fingerprint,
                    record.contract_fingerprint,
                    record.rag_fingerprint,
                    record.routing_category.value,
                    record.status,
                    listing.title,
                    listing.description,
                    listing.location,
                    listing.workplace_type,
                    listing.employment_type,
                    listing.apply_url,
                    record.deterministic_role_level,
                    record.deterministic_role_family,
                    int(record.hard_blocked),
                    json.dumps(record.blocker_categories),
                    record.recommendation,
                    record.proposed_role_level,
                    record.proposed_role_family,
                    record.confidence,
                    record.evidence_grounded,
                    record.citation_grounded,
                    record.failure_category,
                    record.fallback_reason,
                    record.policy_rejection,
                    record.elapsed_ms,
                ),
            )
            assessment_id = cursor.lastrowid
            self._connection.executemany(
                """INSERT INTO shadow_stage_provenance (assessment_id, stage, status, invoked, prior_role_level,
                proposed_role_level, confidence, model, error_category, fallback_reason, tool_names,
                retrieval_count, source_ids, diagnostics) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    (
                        assessment_id,
                        stage.stage,
                        stage.status,
                        int(stage.invoked),
                        stage.prior_role_level,
                        stage.proposed_role_level,
                        stage.confidence,
                        stage.model,
                        stage.error_category,
                        stage.fallback_reason,
                        json.dumps(stage.tool_names),
                        stage.retrieval_count,
                        json.dumps(stage.source_ids),
                        json.dumps(stage.diagnostic_fields),
                    )
                    for stage in record.stages
                ),
            )
            cutoff = _timestamp_text(_utc_now() - timedelta(days=retention_days))
            assert cutoff is not None
            self._connection.execute(
                "DELETE FROM shadow_assessments WHERE observed_at < ?", (cutoff,)
            )

    def record_shadow_run_summary(self, summary: Any) -> None:
        if self._read_only:
            raise RuntimeError("cannot record shadow summary through a read-only repository")
        with self._connection:
            self._connection.execute(
                """INSERT INTO shadow_run_summary (run_at, considered, selected, skipped, attempted, succeeded,
                fallbacks, policy_rejections, rag_used, tool_calls, disagreements) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _timestamp_text(summary.observed_at),
                    summary.considered,
                    summary.selected,
                    json.dumps(summary.skipped),
                    summary.attempted,
                    summary.succeeded,
                    summary.fallbacks,
                    summary.policy_rejections,
                    summary.rag_used,
                    summary.tool_calls,
                    summary.disagreements,
                ),
            )
            self._connection.execute(
                "DELETE FROM shadow_run_summary WHERE id NOT IN (SELECT id FROM shadow_run_summary ORDER BY id DESC LIMIT 30)"
            )

    def shadow_status(self) -> sqlite3.Row | None:
        try:
            return self._connection.execute(  # type: ignore[no-any-return]
                """SELECT
                (SELECT COUNT(*) FROM shadow_assessments) AS persisted,
                (SELECT MAX(run_at) FROM shadow_run_summary) AS last_run,
                (SELECT COALESCE(SUM(status = 'succeeded'), 0) FROM shadow_assessments) AS succeeded,
                (SELECT COALESCE(SUM(status = 'fallback'), 0) FROM shadow_assessments) AS fallbacks,
                (SELECT COALESCE(SUM(status = 'policy_rejected'), 0) FROM shadow_assessments)
                    AS policy_rejections,
                COALESCE((SELECT considered FROM shadow_run_summary ORDER BY id DESC LIMIT 1), 0)
                    AS last_considered,
                COALESCE((SELECT selected FROM shadow_run_summary ORDER BY id DESC LIMIT 1), 0)
                    AS last_selected,
                COALESCE((SELECT attempted FROM shadow_run_summary ORDER BY id DESC LIMIT 1), 0)
                    AS last_attempted,
                COALESCE((SELECT rag_used FROM shadow_run_summary ORDER BY id DESC LIMIT 1), 0)
                    AS last_rag_retrievals,
                COALESCE((SELECT tool_calls FROM shadow_run_summary ORDER BY id DESC LIMIT 1), 0)
                    AS last_tool_calls,
                COALESCE((SELECT disagreements FROM shadow_run_summary ORDER BY id DESC LIMIT 1), 0)
                    AS last_disagreements"""
            ).fetchone()
        except sqlite3.OperationalError:
            return None

    def shadow_review_rows(
        self, limit: int, since: datetime | None = None
    ) -> tuple[sqlite3.Row, ...]:
        query = """SELECT a.*, COALESCE(SUM(p.retrieval_count), 0) AS retrieval_count,
        GROUP_CONCAT(DISTINCT p.stage) AS stages FROM shadow_assessments a
        LEFT JOIN shadow_stage_provenance p ON p.assessment_id = a.id"""
        params: list[object] = []
        if since is not None:
            query += " WHERE a.observed_at >= ?"
            params.append(_timestamp_text(since))
        query += " GROUP BY a.id ORDER BY (a.proposed_role_level IS NOT NULL AND a.proposed_role_level != a.deterministic_role_level) DESC, a.confidence DESC, a.id DESC LIMIT ?"
        params.append(limit)
        try:
            return tuple(self._connection.execute(query, tuple(params)).fetchall())
        except sqlite3.OperationalError:
            return ()

    def shadow_stage_rows(self, assessment_id: int) -> tuple[sqlite3.Row, ...]:
        try:
            return tuple(
                self._connection.execute(
                    "SELECT stage, status, proposed_role_level, confidence, tool_names, retrieval_count, source_ids FROM shadow_stage_provenance WHERE assessment_id = ? ORDER BY id",
                    (assessment_id,),
                ).fetchall()
            )
        except sqlite3.OperationalError:
            return ()

    def listing_state_counts(self) -> ListingStateCounts:
        row = self._connection.execute(
            "SELECT COUNT(*) AS total_known, COALESCE(SUM(active), 0) AS active FROM job_state"
        ).fetchone()
        assert row is not None
        total_known = int(row["total_known"])
        active = int(row["active"])
        return ListingStateCounts(
            total_known=total_known, active=active, inactive=total_known - active
        )

    def record_monitor_summary(self, summary: MonitorRunSummary) -> None:
        """Persist one safe monitor summary and retain only the most recent 30."""
        if self._read_only:
            raise RuntimeError("cannot record monitor summary through a read-only repository")
        _require_aware(summary.run_at)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO monitor_run_summary (
                    run_at, sources_configured, sources_successful, sources_authoritative,
                    sources_degraded, sources_failed, listings_seen, listings_new,
                    listings_updated, listings_reposted, listings_reappeared, listings_unchanged,
                    opportunities, assessments, alerts_queued
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _timestamp_text(summary.run_at),
                    summary.sources_configured,
                    summary.sources_successful,
                    summary.sources_authoritative,
                    summary.sources_degraded,
                    summary.sources_failed,
                    summary.listings_seen,
                    summary.listings_new,
                    summary.listings_updated,
                    summary.listings_reposted,
                    summary.listings_reappeared,
                    summary.listings_unchanged,
                    summary.opportunities,
                    summary.assessments,
                    summary.alerts_queued,
                ),
            )
            self._connection.execute(
                """DELETE FROM monitor_run_summary WHERE id NOT IN (
                    SELECT id FROM monitor_run_summary ORDER BY id DESC LIMIT 30
                )"""
            )

    def latest_monitor_summary(self) -> MonitorRunSummary | None:
        try:
            row = self._connection.execute(
                """
                SELECT run_at, sources_configured, sources_successful,
                       COALESCE(sources_authoritative, sources_successful) AS sources_authoritative,
                       COALESCE(sources_degraded, 0) AS sources_degraded,
                       sources_failed, listings_seen, listings_new, listings_updated,
                       listings_reposted, listings_reappeared, listings_unchanged,
                       opportunities, assessments, alerts_queued
                FROM monitor_run_summary ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        return _monitor_summary_from_row(row) if row is not None else None

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS job_state (
                source TEXT NOT NULL, company TEXT NOT NULL, source_job_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, posted_at TEXT, active INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                PRIMARY KEY (source, company, source_job_id)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS monitor_run_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL,
                sources_configured INTEGER NOT NULL, sources_successful INTEGER NOT NULL,
                sources_authoritative INTEGER NOT NULL DEFAULT 0,
                sources_degraded INTEGER NOT NULL DEFAULT 0,
                sources_failed INTEGER NOT NULL, listings_seen INTEGER NOT NULL,
                listings_new INTEGER NOT NULL, listings_updated INTEGER NOT NULL,
                listings_reposted INTEGER NOT NULL, listings_reappeared INTEGER NOT NULL,
                listings_unchanged INTEGER NOT NULL, opportunities INTEGER NOT NULL,
                assessments INTEGER NOT NULL, alerts_queued INTEGER NOT NULL
            )
            """
        )
        self._ensure_summary_columns()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_run_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL,
                company TEXT NOT NULL, observed_at TEXT NOT NULL, status TEXT NOT NULL,
                authoritative INTEGER NOT NULL, listing_count INTEGER NOT NULL,
                previous_active_count INTEGER NOT NULL, attempt_count INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL, failure_category TEXT
            )
            """
        )
        self._connection.execute(
            """CREATE INDEX IF NOT EXISTS source_run_health_identity
            ON source_run_health (source_type, company, id DESC)"""
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL, company TEXT NOT NULL, source_job_id TEXT NOT NULL,
                observed_at TEXT NOT NULL, semantic_fingerprint TEXT NOT NULL,
                deterministic_fingerprint TEXT NOT NULL, contract_fingerprint TEXT NOT NULL,
                rag_fingerprint TEXT, routing_category TEXT NOT NULL, status TEXT NOT NULL,
                title TEXT NOT NULL, description TEXT NOT NULL, location TEXT,
                workplace_type TEXT, employment_type TEXT, apply_url TEXT NOT NULL,
                deterministic_role_level TEXT NOT NULL, deterministic_role_family TEXT,
                hard_blocked INTEGER NOT NULL, blocker_categories TEXT NOT NULL,
                recommendation TEXT NOT NULL, proposed_role_level TEXT,
                proposed_role_family TEXT, confidence REAL, evidence_grounded INTEGER,
                citation_grounded INTEGER, failure_category TEXT, fallback_reason TEXT,
                policy_rejection TEXT, elapsed_ms REAL
            )
            """
        )
        self._connection.execute(
            """CREATE INDEX IF NOT EXISTS shadow_assessments_dedupe
            ON shadow_assessments (
                source, company, source_job_id, semantic_fingerprint,
                deterministic_fingerprint, contract_fingerprint
            )"""
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_stage_provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT, assessment_id INTEGER NOT NULL,
                stage TEXT NOT NULL, status TEXT NOT NULL, invoked INTEGER NOT NULL,
                prior_role_level TEXT NOT NULL, proposed_role_level TEXT,
                confidence REAL, model TEXT, error_category TEXT, fallback_reason TEXT,
                tool_names TEXT NOT NULL, retrieval_count INTEGER NOT NULL,
                source_ids TEXT NOT NULL, diagnostics TEXT NOT NULL,
                FOREIGN KEY (assessment_id) REFERENCES shadow_assessments(id)
                    ON DELETE CASCADE
            )
            """
        )
        self._connection.execute(
            """CREATE INDEX IF NOT EXISTS shadow_stage_provenance_assessment
            ON shadow_stage_provenance (assessment_id, id)"""
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_run_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL,
                considered INTEGER NOT NULL, selected INTEGER NOT NULL, skipped TEXT NOT NULL,
                attempted INTEGER NOT NULL, succeeded INTEGER NOT NULL,
                fallbacks INTEGER NOT NULL, policy_rejections INTEGER NOT NULL,
                rag_used INTEGER NOT NULL, tool_calls INTEGER NOT NULL,
                disagreements INTEGER NOT NULL
            )
            """
        )

    def _ensure_summary_columns(self) -> None:
        existing = {
            row[1] for row in self._connection.execute("PRAGMA table_info(monitor_run_summary)")
        }
        if "sources_authoritative" not in existing:
            self._connection.execute(
                "ALTER TABLE monitor_run_summary ADD COLUMN sources_authoritative INTEGER NOT NULL DEFAULT 0"
            )
            self._connection.execute(
                "UPDATE monitor_run_summary SET sources_authoritative = sources_successful"
            )
        if "sources_degraded" not in existing:
            self._connection.execute(
                "ALTER TABLE monitor_run_summary ADD COLUMN sources_degraded INTEGER NOT NULL DEFAULT 0"
            )

    def _validate_snapshot(
        self, listings: Sequence[JobListing], source_type: str, company: str
    ) -> None:
        observed_keys: set[str] = set()
        for listing in listings:
            _validate_source_identity(listing, source_type, company)
            key = _listing_key(listing)
            if key in observed_keys:
                raise ValueError("a successful source run cannot contain duplicate listing keys")
            observed_keys.add(key)

    def _existing_listing(self, listing: JobListing) -> sqlite3.Row | None:
        return self._connection.execute(  # type: ignore[no-any-return]
            """SELECT fingerprint, posted_at, active FROM job_state
            WHERE source = ? AND company = ? AND source_job_id = ?""",
            (listing.source, listing.company, listing.source_job_id),
        ).fetchone()

    def _change_for_listing(self, listing: JobListing) -> ListingChange:
        existing = self._existing_listing(listing)
        if existing is None:
            return ListingChange.NEW
        return _change_for_existing_listing(
            existing, _fingerprint(listing), _timestamp_text(listing.posted_at)
        )

    def _record_listing(self, listing: JobListing, observed_at: datetime) -> ListingChange:
        existing = self._existing_listing(listing)
        fingerprint = _fingerprint(listing)
        posted_at = _timestamp_text(listing.posted_at)
        observed_at_text = _timestamp_text(observed_at)
        assert observed_at_text is not None
        if existing is None:
            self._connection.execute(
                """INSERT INTO job_state (source, company, source_job_id, fingerprint, posted_at,
                active, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    listing.source,
                    listing.company,
                    listing.source_job_id,
                    fingerprint,
                    posted_at,
                    observed_at_text,
                    observed_at_text,
                ),
            )
            return ListingChange.NEW
        change = _change_for_existing_listing(existing, fingerprint, posted_at)
        self._connection.execute(
            """UPDATE job_state SET fingerprint = ?, posted_at = ?, active = 1, last_seen_at = ?
            WHERE source = ? AND company = ? AND source_job_id = ?""",
            (
                fingerprint,
                posted_at,
                observed_at_text,
                listing.source,
                listing.company,
                listing.source_job_id,
            ),
        )
        return change

    def _mark_unseen_listings_inactive(
        self, source_type: str, company: str, observed_keys: set[str]
    ) -> None:
        rows = self._connection.execute(
            "SELECT source_job_id FROM job_state WHERE source = ? AND company = ? AND active = 1",
            (source_type, company),
        ).fetchall()
        unseen_ids = [
            row["source_job_id"] for row in rows if row["source_job_id"] not in observed_keys
        ]
        self._connection.executemany(
            """UPDATE job_state SET active = 0 WHERE source = ? AND company = ? AND source_job_id = ?""",
            ((source_type, company, source_job_id) for source_job_id in unseen_ids),
        )


def _source_health_summary_from_row(row: sqlite3.Row) -> SourceHealthSummary:
    last_success = row["last_authoritative_success_at"]
    return SourceHealthSummary(
        source_type=str(row["source_type"]),
        company=str(row["company"]),
        status=SourceHealthStatus(row["status"]),
        authoritative=bool(row["authoritative"]),
        listing_count=int(row["listing_count"]),
        previous_active_count=int(row["previous_active_count"]),
        attempt_count=int(row["attempt_count"]),
        duration_ms=int(row["duration_ms"]),
        failure_category=str(row["failure_category"])
        if row["failure_category"] is not None
        else None,
        observed_at=_parse_timestamp(row["observed_at"]),
        last_authoritative_success_at=_parse_timestamp(last_success)
        if last_success is not None
        else None,
        recent_issue_count=int(row["recent_issue_count"]),
    )


def _monitor_summary_from_row(row: sqlite3.Row) -> MonitorRunSummary:
    return MonitorRunSummary(
        run_at=_parse_timestamp(row["run_at"]),
        sources_configured=int(row["sources_configured"]),
        sources_successful=int(row["sources_successful"]),
        sources_authoritative=int(row["sources_authoritative"]),
        sources_degraded=int(row["sources_degraded"]),
        sources_failed=int(row["sources_failed"]),
        listings_seen=int(row["listings_seen"]),
        listings_new=int(row["listings_new"]),
        listings_updated=int(row["listings_updated"]),
        listings_reposted=int(row["listings_reposted"]),
        listings_reappeared=int(row["listings_reappeared"]),
        listings_unchanged=int(row["listings_unchanged"]),
        opportunities=int(row["opportunities"]),
        assessments=int(row["assessments"]),
        alerts_queued=int(row["alerts_queued"]),
    )


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("state contains an invalid timestamp")
    timestamp = datetime.fromisoformat(value)
    _require_aware(timestamp)
    return timestamp


def _change_for_existing_listing(
    existing: sqlite3.Row, fingerprint: str, posted_at: str | None
) -> ListingChange:
    if not bool(existing["active"]):
        return ListingChange.REAPPEARED
    if _is_newer_timestamp(posted_at, existing["posted_at"]):
        return ListingChange.REPOSTED
    if fingerprint != existing["fingerprint"]:
        return ListingChange.UPDATED
    return ListingChange.UNCHANGED


def _fingerprint(listing: JobListing) -> str:
    payload = {
        "title": listing.title,
        "description": listing.description,
        "apply_url": listing.apply_url,
        "location": listing.location,
        "workplace_type": listing.workplace_type,
        "employment_type": listing.employment_type,
        "posted_at": _timestamp_text(listing.posted_at),
        "deadline_at": _timestamp_text(listing.deadline_at),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _listing_key(listing: JobListing) -> str:
    return listing.source_job_id


def _is_newer_timestamp(current: str | None, previous: object) -> bool:
    return (
        current is not None
        and isinstance(previous, str)
        and datetime.fromisoformat(current) > datetime.fromisoformat(previous)
    )


def _timestamp_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    _require_aware(value)
    return value.astimezone(UTC).isoformat()


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("state timestamps must include timezone information")


def _validate_source_identity(listing: JobListing, source_type: str, company: str) -> None:
    if listing.source != source_type or listing.company != company:
        raise ValueError("listing identity does not match the successful source run")
