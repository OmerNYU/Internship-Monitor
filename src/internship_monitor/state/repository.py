"""Small SQLite repository for stable job-listing state transitions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from internship_monitor.models import JobListing
from internship_monitor.reporting.models import ListingStateCounts, MonitorRunSummary
from internship_monitor.state.models import ListingChange, ListingObservation


def _utc_now() -> datetime:
    return datetime.now(UTC)


class JobStateRepository:
    """Persist minimal listing fingerprints; it never stores configuration or credentials."""

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        state_path = Path(path)
        self._read_only = read_only
        if read_only and state_path.exists():
            self._connection = sqlite3.connect(f"{state_path.resolve().as_uri()}?mode=ro", uri=True)
        elif read_only:
            self._connection = sqlite3.connect(":memory:")
            self._create_schema()
        else:
            self._connection = sqlite3.connect(state_path)
            self._create_schema()
        self._connection.row_factory = sqlite3.Row

    def close(self) -> None:
        """Close the SQLite connection held by this repository."""
        self._connection.close()

    def __enter__(self) -> JobStateRepository:
        return self

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        self.close()

    def compare_successful_source_run(
        self,
        listings: Sequence[JobListing],
        *,
        source_type: str,
        company: str,
    ) -> tuple[ListingObservation, ...]:
        """Compare a successful snapshot without changing database state."""
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
        """Record a successful source snapshot and return transitions in input order.

        Listings not present in this successful snapshot become inactive internally. They are not
        surfaced as a separate "removed" outcome. If one later appears again, it is reported as
        ``reappeared``. Failed source runs must never call this method.
        """
        if self._read_only:
            raise RuntimeError("cannot record listing state through a read-only repository")

        timestamp = observed_at or _utc_now()
        _require_aware(timestamp)
        self._validate_snapshot(listings, source_type, company)
        observed_keys = {_listing_key(listing) for listing in listings}

        with self._connection:
            observations = tuple(
                ListingObservation(
                    listing=listing,
                    change=self._record_listing(listing, timestamp),
                )
                for listing in listings
            )
            self._mark_unseen_listings_inactive(source_type, company, observed_keys)

        return observations

    def listing_state_counts(self) -> ListingStateCounts:
        """Return aggregate listing state without exposing any listing content."""
        row = self._connection.execute(
            """
            SELECT COUNT(*) AS total_known,
                   COALESCE(SUM(active), 0) AS active
            FROM job_state
            """
        ).fetchone()
        assert row is not None
        total_known = int(row["total_known"])
        active = int(row["active"])
        return ListingStateCounts(
            total_known=total_known,
            active=active,
            inactive=total_known - active,
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
                    run_at, sources_configured, sources_successful, sources_failed,
                    listings_seen, listings_new, listings_updated, listings_reposted,
                    listings_reappeared, listings_unchanged, opportunities, assessments,
                    alerts_queued
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _timestamp_text(summary.run_at),
                    summary.sources_configured,
                    summary.sources_successful,
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
                """
                DELETE FROM monitor_run_summary
                WHERE id NOT IN (
                    SELECT id
                    FROM monitor_run_summary
                    ORDER BY id DESC
                    LIMIT 30
                )
                """
            )

    def latest_monitor_summary(self) -> MonitorRunSummary | None:
        """Return the newest safe monitor summary, if one has been recorded."""
        try:
            row = self._connection.execute(
                """
                SELECT run_at, sources_configured, sources_successful, sources_failed,
                       listings_seen, listings_new, listings_updated, listings_reposted,
                       listings_reappeared, listings_unchanged, opportunities, assessments,
                       alerts_queued
                FROM monitor_run_summary
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        return _monitor_summary_from_row(row) if row is not None else None

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS job_state (
                source TEXT NOT NULL,
                company TEXT NOT NULL,
                source_job_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                posted_at TEXT,
                active INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (source, company, source_job_id)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS monitor_run_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                sources_configured INTEGER NOT NULL,
                sources_successful INTEGER NOT NULL,
                sources_failed INTEGER NOT NULL,
                listings_seen INTEGER NOT NULL,
                listings_new INTEGER NOT NULL,
                listings_updated INTEGER NOT NULL,
                listings_reposted INTEGER NOT NULL,
                listings_reappeared INTEGER NOT NULL,
                listings_unchanged INTEGER NOT NULL,
                opportunities INTEGER NOT NULL,
                assessments INTEGER NOT NULL,
                alerts_queued INTEGER NOT NULL
            )
            """
        )

    def _validate_snapshot(
        self,
        listings: Sequence[JobListing],
        source_type: str,
        company: str,
    ) -> None:
        observed_keys: set[str] = set()
        for listing in listings:
            _validate_source_identity(listing, source_type, company)
            key = _listing_key(listing)
            if key in observed_keys:
                raise ValueError("a successful source run cannot contain duplicate listing keys")
            observed_keys.add(key)

    def _existing_listing(self, listing: JobListing) -> sqlite3.Row | None:
        existing: sqlite3.Row | None = self._connection.execute(
            """
            SELECT fingerprint, posted_at, active
            FROM job_state
            WHERE source = ? AND company = ? AND source_job_id = ?
            """,
            (listing.source, listing.company, listing.source_job_id),
        ).fetchone()
        return existing

    def _change_for_listing(self, listing: JobListing) -> ListingChange:
        existing = self._existing_listing(listing)
        if existing is None:
            return ListingChange.NEW
        return _change_for_existing_listing(
            existing,
            _fingerprint(listing),
            _timestamp_text(listing.posted_at),
        )

    def _record_listing(self, listing: JobListing, observed_at: datetime) -> ListingChange:
        existing = self._existing_listing(listing)
        fingerprint = _fingerprint(listing)
        posted_at = _timestamp_text(listing.posted_at)
        observed_at_text = _timestamp_text(observed_at)
        assert observed_at_text is not None

        if existing is None:
            self._connection.execute(
                """
                INSERT INTO job_state (
                    source, company, source_job_id, fingerprint, posted_at, active,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
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
            """
            UPDATE job_state
            SET fingerprint = ?, posted_at = ?, active = 1, last_seen_at = ?
            WHERE source = ? AND company = ? AND source_job_id = ?
            """,
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
        self,
        source_type: str,
        company: str,
        observed_keys: set[str],
    ) -> None:
        rows = self._connection.execute(
            """
            SELECT source_job_id
            FROM job_state
            WHERE source = ? AND company = ? AND active = 1
            """,
            (source_type, company),
        ).fetchall()
        unseen_ids = [
            row["source_job_id"] for row in rows if row["source_job_id"] not in observed_keys
        ]
        self._connection.executemany(
            """
            UPDATE job_state
            SET active = 0
            WHERE source = ? AND company = ? AND source_job_id = ?
            """,
            ((source_type, company, source_job_id) for source_job_id in unseen_ids),
        )


def _monitor_summary_from_row(row: sqlite3.Row) -> MonitorRunSummary:
    return MonitorRunSummary(
        run_at=_parse_timestamp(row["run_at"]),
        sources_configured=int(row["sources_configured"]),
        sources_successful=int(row["sources_successful"]),
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
        raise ValueError("monitor summary contains an invalid timestamp")
    timestamp = datetime.fromisoformat(value)
    _require_aware(timestamp)
    return timestamp


def _change_for_existing_listing(
    existing: sqlite3.Row,
    fingerprint: str,
    posted_at: str | None,
) -> ListingChange:
    if not bool(existing["active"]):
        return ListingChange.REAPPEARED
    if _is_newer_timestamp(posted_at, existing["posted_at"]):
        return ListingChange.REPOSTED
    if fingerprint != existing["fingerprint"]:
        return ListingChange.UPDATED
    return ListingChange.UNCHANGED


def _fingerprint(listing: JobListing) -> str:
    """Hash relevant canonical fields while ignoring each polling run's discovery time."""
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
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _listing_key(listing: JobListing) -> str:
    return listing.source_job_id


def _is_newer_timestamp(current: str | None, previous: object) -> bool:
    if current is None or not isinstance(previous, str):
        return False
    return datetime.fromisoformat(current) > datetime.fromisoformat(previous)


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
