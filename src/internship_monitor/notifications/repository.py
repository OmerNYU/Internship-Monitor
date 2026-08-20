"""Durable, secret-free SQLite queue for scheduled notification content."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from internship_monitor.notifications.models import (
    DeliveryReport,
    DigestCandidateState,
    Notification,
    NotificationKind,
    QueuedNotification,
    QueueStatus,
)
from internship_monitor.reporting.models import DeliveryRunSummary, NotificationQueueCounts

PAKISTAN_TIME = ZoneInfo("Asia/Karachi")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class NotificationQueueRepository:
    """Persist due notifications and outcomes without retaining provider credentials."""

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        state_path = Path(path)
        self._read_only = read_only
        if read_only and state_path.exists():
            self._connection = sqlite3.connect(f"{state_path.resolve().as_uri()}?mode=ro", uri=True)
        elif read_only:
            self._connection = sqlite3.connect(":memory:")
        else:
            self._connection = sqlite3.connect(state_path)
        self._connection.row_factory = sqlite3.Row
        if read_only and not state_path.exists():
            self._create_schema()
        elif not read_only:
            self._create_schema()
            self._migrate_schema()

    def close(self) -> None:
        """Close the held SQLite connection."""
        self._connection.close()

    def __enter__(self) -> NotificationQueueRepository:
        return self

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        self.close()

    def enqueue(
        self,
        notification: Notification,
        *,
        due_at: datetime,
        queued_at: datetime | None = None,
        kind: NotificationKind = NotificationKind.ALERT,
        digest_key: str | None = None,
        digest_category: str | None = None,
        digest_payload: str | None = None,
        digest_recap_key: str | None = None,
    ) -> bool:
        """Store a notification once, returning whether it was newly queued."""
        self._require_writable()
        _require_aware(due_at)
        queued = queued_at or _utc_now()
        _require_aware(queued)
        candidate_state = (
            DigestCandidateState.PENDING_DIGEST
            if kind is NotificationKind.DIGEST_CANDIDATE
            else None
        )
        next_attempt_at = None if candidate_state is not None else due_at
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO notification_queue (
                    idempotency_key, subject, body, due_at, queued_at, attempts, status,
                    next_attempt_at, kind, digest_key, candidate_state, digest_category,
                   digest_payload, included_digest_key, digest_recap_key
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    notification.idempotency_key,
                    notification.subject,
                    notification.body,
                    _timestamp_text(due_at),
                    _timestamp_text(queued),
                    QueueStatus.PENDING.value,
                    _timestamp_text(next_attempt_at),
                    kind.value,
                    digest_key,
                    candidate_state.value if candidate_state is not None else None,
                    digest_category,
                    digest_payload,
                    digest_recap_key,
                ),
            )
        return cursor.rowcount == 1

    def due(self, *, now: datetime | None = None) -> tuple[QueuedNotification, ...]:
        """Return non-candidate notifications whose delivery or retry time has arrived."""
        moment = now or _utc_now()
        _require_aware(moment)
        rows = self._connection.execute(
            """
            SELECT idempotency_key, subject, body, due_at, queued_at, attempts, status,
                   next_attempt_at, kind, digest_key, candidate_state, digest_category,
                   digest_payload, included_digest_key, digest_recap_key
            FROM notification_queue
            WHERE status = ? AND kind IN (?, ?) AND next_attempt_at <= ?
            ORDER BY due_at, queued_at, idempotency_key
            """,
            (
                QueueStatus.PENDING.value,
                NotificationKind.ALERT.value,
                NotificationKind.DAILY_DIGEST.value,
                _timestamp_text(moment),
            ),
        ).fetchall()
        return tuple(_queued_notification(row) for row in rows)

    def due_digest_keys(self, *, now: datetime | None = None) -> tuple[str, ...]:
        """Return every digest date with candidates due for first composition."""
        moment = now or _utc_now()
        _require_aware(moment)
        rows = self._connection.execute(
            """
            SELECT DISTINCT digest_key
            FROM notification_queue
            WHERE kind = ? AND candidate_state = ? AND due_at <= ?
            ORDER BY digest_key
            """,
            (
                NotificationKind.DIGEST_CANDIDATE.value,
                DigestCandidateState.PENDING_DIGEST.value,
                _timestamp_text(moment),
            ),
        ).fetchall()
        return tuple(row["digest_key"] for row in rows if isinstance(row["digest_key"], str))

    def digest_candidates(self, digest_key: str) -> tuple[QueuedNotification, ...]:
        """Return candidates that have not yet been included in their daily digest."""
        rows = self._connection.execute(
            """
            SELECT idempotency_key, subject, body, due_at, queued_at, attempts, status,
                   next_attempt_at, kind, digest_key, candidate_state, digest_category,
                   digest_payload, included_digest_key, digest_recap_key
            FROM notification_queue
            WHERE kind = ? AND digest_key = ? AND candidate_state = ?
            ORDER BY digest_category, queued_at, idempotency_key
            """,
            (
                NotificationKind.DIGEST_CANDIDATE.value,
                digest_key,
                DigestCandidateState.PENDING_DIGEST.value,
            ),
        ).fetchall()
        return tuple(_queued_notification(row) for row in rows)

    def composition_candidates(
        self, digest_key: str, *, now: datetime
    ) -> tuple[QueuedNotification, ...]:
        """Return all due pending candidates for one PKT digest, including catch-up."""
        rows = self._connection.execute(
            """
            SELECT idempotency_key, subject, body, due_at, queued_at, attempts, status,
                   next_attempt_at, kind, digest_key, candidate_state, digest_category,
                   digest_payload, included_digest_key, digest_recap_key
            FROM notification_queue
            WHERE kind = ? AND candidate_state = ? AND due_at <= ? AND digest_key <= ?
            ORDER BY digest_key, queued_at, idempotency_key
            """,
            (
                NotificationKind.DIGEST_CANDIDATE.value,
                DigestCandidateState.PENDING_DIGEST.value,
                _timestamp_text(now),
                digest_key,
            ),
        ).fetchall()
        return tuple(_queued_notification(row) for row in rows)

    def immediate_alert_recaps(self, digest_key: str) -> tuple[QueuedNotification, ...]:
        rows = self._connection.execute(
            """
            SELECT idempotency_key, subject, body, due_at, queued_at, attempts, status,
                   next_attempt_at, kind, digest_key, candidate_state, digest_category,
                   digest_payload, included_digest_key, digest_recap_key
            FROM notification_queue
            WHERE kind = ? AND digest_recap_key = ?
            ORDER BY queued_at, idempotency_key
            """,
            (NotificationKind.ALERT.value, digest_key),
        ).fetchall()
        return tuple(_queued_notification(row) for row in rows)

    def create_daily_digest(
        self,
        notification: Notification,
        candidates: tuple[QueuedNotification, ...],
        *,
        digest_payload: str,
        due_at: datetime,
        queued_at: datetime | None = None,
    ) -> bool:
        """Atomically create one digest and mark its candidate membership."""
        self._require_writable()
        _require_aware(due_at)
        queued = queued_at or _utc_now()
        _require_aware(queued)
        digest_key = notification.idempotency_key
        candidate_keys = tuple(candidate.notification.idempotency_key for candidate in candidates)
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO notification_queue (
                    idempotency_key, subject, body, due_at, queued_at, attempts, status,
                    next_attempt_at, kind, digest_key, candidate_state, digest_category,
                    digest_payload, included_digest_key, digest_recap_key
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, NULL, NULL, ?, NULL, NULL)
                """,
                (
                    digest_key,
                    notification.subject,
                    notification.body,
                    _timestamp_text(due_at),
                    _timestamp_text(queued),
                    QueueStatus.PENDING.value,
                    _timestamp_text(due_at),
                    NotificationKind.DAILY_DIGEST.value,
                    digest_key,
                    digest_payload,
                ),
            )
            if cursor.rowcount != 1:
                return False
            if candidate_keys:
                placeholders = ", ".join("?" for _ in candidate_keys)
                self._connection.execute(
                    f"""
                    UPDATE notification_queue
                    SET candidate_state = ?, included_digest_key = ?
                    WHERE idempotency_key IN ({placeholders}) AND candidate_state = ?
                    """,
                    (
                        DigestCandidateState.INCLUDED_IN_DIGEST.value,
                        digest_key,
                        *candidate_keys,
                        DigestCandidateState.PENDING_DIGEST.value,
                    ),
                )
        return True

    def latest_daily_digest(self) -> QueuedNotification | None:
        row = self._connection.execute(
            """
            SELECT idempotency_key, subject, body, due_at, queued_at, attempts, status,
                   next_attempt_at, kind, digest_key, candidate_state, digest_category,
                   digest_payload, included_digest_key, digest_recap_key
            FROM notification_queue WHERE kind = ? ORDER BY queued_at DESC LIMIT 1
            """,
            (NotificationKind.DAILY_DIGEST.value,),
        ).fetchone()
        return _queued_notification(row) if row is not None else None

    def get(self, idempotency_key: str) -> QueuedNotification | None:
        """Return one queued notification without exposing provider configuration."""
        row = self._connection.execute(
            """
            SELECT idempotency_key, subject, body, due_at, queued_at, attempts, status,
                   next_attempt_at, kind, digest_key, candidate_state, digest_category,
                   digest_payload, included_digest_key, digest_recap_key
            FROM notification_queue
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        return _queued_notification(row) if row is not None else None

    def record_delivery(
        self,
        report: DeliveryReport,
        *,
        attempted_at: datetime | None = None,
        max_attempts: int = 3,
        retry_delay: timedelta = timedelta(minutes=15),
    ) -> QueueStatus:
        """Persist one delivery outcome and return its new durable state."""
        self._require_writable()
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if retry_delay <= timedelta():
            raise ValueError("retry_delay must be positive")

        moment = attempted_at or _utc_now()
        _require_aware(moment)
        current = self.get(report.notification.idempotency_key)
        if current is None:
            raise ValueError("cannot record delivery for a notification that is not queued")
        if current.status is not QueueStatus.PENDING:
            return current.status

        attempts = current.attempts + 1
        if report.delivered:
            status = QueueStatus.DELIVERED
            next_attempt_at: datetime | None = None
        elif attempts >= max_attempts:
            status = QueueStatus.FAILED
            next_attempt_at = None
        else:
            status = QueueStatus.PENDING
            next_attempt_at = moment + retry_delay * (2 ** (attempts - 1))

        with self._connection:
            self._connection.execute(
                """
                UPDATE notification_queue
                SET attempts = ?, status = ?, next_attempt_at = ?
                WHERE idempotency_key = ?
                """,
                (
                    attempts,
                    status.value,
                    _timestamp_text(next_attempt_at),
                    report.notification.idempotency_key,
                ),
            )
            if (
                current.kind is NotificationKind.DAILY_DIGEST
                and status is QueueStatus.DELIVERED
                and current.digest_key is not None
            ):
                self._connection.execute(
                    """
                    UPDATE notification_queue
                    SET candidate_state = ?
                    WHERE kind = ? AND included_digest_key = ? AND candidate_state = ?
                    """,
                    (
                        DigestCandidateState.DIGEST_DELIVERED.value,
                        NotificationKind.DIGEST_CANDIDATE.value,
                        current.digest_key,
                        DigestCandidateState.INCLUDED_IN_DIGEST.value,
                    ),
                )
        return status

    def queue_counts(self, *, now: datetime | None = None) -> NotificationQueueCounts:
        """Return safe aggregate queue health without exposing notification content."""
        moment = now or _utc_now()
        _require_aware(moment)
        row = self._connection.execute(
            """
            SELECT
                COALESCE(SUM(
                    CASE
                        WHEN status = ? AND kind IN (?, ?) AND next_attempt_at <= ? THEN 1
                        ELSE 0
                    END
                ), 0) AS due_now,
                COALESCE(SUM(
                    CASE
                        WHEN status = ? AND attempts = 0 AND next_attempt_at > ? THEN 1
                        ELSE 0
                    END
                ), 0) AS scheduled,
                COALESCE(SUM(
                    CASE
                        WHEN status = ? AND attempts > 0 THEN 1
                        ELSE 0
                    END
                ), 0) AS retries_pending,
                COALESCE(SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), 0) AS terminal_failures,
                COALESCE(SUM(CASE WHEN kind = ? THEN 1 ELSE 0 END), 0) AS digest_candidates,
                COALESCE(SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), 0) AS delivered
            FROM notification_queue
            """,
            (
                QueueStatus.PENDING.value,
                NotificationKind.ALERT.value,
                NotificationKind.DAILY_DIGEST.value,
                _timestamp_text(moment),
                QueueStatus.PENDING.value,
                _timestamp_text(moment),
                QueueStatus.PENDING.value,
                QueueStatus.FAILED.value,
                NotificationKind.DIGEST_CANDIDATE.value,
                QueueStatus.DELIVERED.value,
            ),
        ).fetchone()
        assert row is not None
        latest = self.latest_daily_digest()
        latest_candidates = 0
        if latest is not None and latest.digest_payload is not None:
            try:
                latest_candidates = int(
                    json.loads(latest.digest_payload).get("total_included_opportunities", 0)
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                latest_candidates = 0
        local = moment.astimezone(PAKISTAN_TIME)
        if (local.hour, local.minute) < (11, 0):
            next_local = local.replace(hour=11, minute=0, second=0, microsecond=0)
        else:
            next_local = (local + timedelta(days=1)).replace(
                hour=11, minute=0, second=0, microsecond=0
            )
        return NotificationQueueCounts(
            due_now=int(row["due_now"]),
            scheduled=int(row["scheduled"]),
            retries_pending=int(row["retries_pending"]),
            terminal_failures=int(row["terminal_failures"]),
            digest_candidates=int(row["digest_candidates"]),
            delivered=int(row["delivered"]),
            latest_digest_key=latest.digest_key if latest is not None else None,
            latest_digest_status=latest.status.value if latest is not None else None,
            latest_digest_candidates=latest_candidates,
            next_digest_eligible_at=next_local,
        )

    def record_delivery_summary(self, summary: DeliveryRunSummary) -> None:
        """Persist one safe delivery summary and retain only the newest 30."""
        self._require_writable()
        _require_aware(summary.run_at)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO delivery_run_summary (
                    run_at, due_notifications, notifications_delivered,
                    retries_pending, terminal_failures
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _timestamp_text(summary.run_at),
                    summary.due_notifications,
                    summary.notifications_delivered,
                    summary.retries_pending,
                    summary.terminal_failures,
                ),
            )
            self._connection.execute(
                """
                DELETE FROM delivery_run_summary
                WHERE id NOT IN (
                    SELECT id
                    FROM delivery_run_summary
                    ORDER BY id DESC
                    LIMIT 30
                )
                """
            )

    def latest_delivery_summary(self) -> DeliveryRunSummary | None:
        """Return the newest safe delivery summary, if one has been recorded."""
        try:
            row = self._connection.execute(
                """
                SELECT run_at, due_notifications, notifications_delivered,
                       retries_pending, terminal_failures
                FROM delivery_run_summary
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        return DeliveryRunSummary(
            run_at=_parse_timestamp(row["run_at"]),
            due_notifications=int(row["due_notifications"]),
            notifications_delivered=int(row["notifications_delivered"]),
            retries_pending=int(row["retries_pending"]),
            terminal_failures=int(row["terminal_failures"]),
        )

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_queue (
                idempotency_key TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                due_at TEXT NOT NULL,
                queued_at TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                status TEXT NOT NULL,
                next_attempt_at TEXT,
                kind TEXT NOT NULL DEFAULT 'alert',
                digest_key TEXT,
                candidate_state TEXT,
                digest_category TEXT,
                digest_payload TEXT,
                included_digest_key TEXT,
                digest_recap_key TEXT
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_run_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                due_notifications INTEGER NOT NULL,
                notifications_delivered INTEGER NOT NULL,
                retries_pending INTEGER NOT NULL,
                terminal_failures INTEGER NOT NULL
            )
            """
        )

    def _migrate_schema(self) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(notification_queue)").fetchall()
        }
        additions = {
            "kind": "TEXT NOT NULL DEFAULT 'alert'",
            "digest_key": "TEXT",
            "candidate_state": "TEXT",
            "digest_category": "TEXT",
            "digest_payload": "TEXT",
            "included_digest_key": "TEXT",
            "digest_recap_key": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                self._connection.execute(
                    f"ALTER TABLE notification_queue ADD COLUMN {name} {definition}"
                )

    def _require_writable(self) -> None:
        if self._read_only:
            raise RuntimeError("cannot write notification queue through a read-only repository")


def _digest_key_for_candidates(candidates: tuple[QueuedNotification, ...]) -> str:
    digest_keys = {candidate.digest_key for candidate in candidates}
    if len(digest_keys) != 1 or None in digest_keys:
        raise ValueError("daily digest candidates must have one stable digest identity")
    digest_key = next(iter(digest_keys))
    assert digest_key is not None
    return digest_key


def _queued_notification(row: sqlite3.Row) -> QueuedNotification:
    kind = NotificationKind(row["kind"])
    candidate_state = (
        DigestCandidateState(row["candidate_state"]) if row["candidate_state"] is not None else None
    )
    return QueuedNotification(
        notification=Notification(
            idempotency_key=row["idempotency_key"],
            decision=None,
            subject=row["subject"],
            body=row["body"],
        ),
        due_at=_parse_timestamp(row["due_at"]),
        queued_at=_parse_timestamp(row["queued_at"]),
        attempts=row["attempts"],
        status=QueueStatus(row["status"]),
        next_attempt_at=(
            _parse_timestamp(row["next_attempt_at"]) if row["next_attempt_at"] is not None else None
        ),
        kind=kind,
        digest_key=row["digest_key"],
        candidate_state=candidate_state,
        digest_category=row["digest_category"],
        digest_payload=row["digest_payload"],
        included_digest_key=row["included_digest_key"],
        digest_recap_key=row["digest_recap_key"],
    )


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("notification queue contains an invalid timestamp")
    timestamp = datetime.fromisoformat(value)
    _require_aware(timestamp)
    return timestamp


def _timestamp_text(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("notification queue times must include timezone information")
