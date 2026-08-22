"""Durable at-least-once delivery for rescue producer events.

Producers persist a validated event before attempting the network. Delivery may be
retried because the collector de-duplicates exact ``event_id`` replays. Permanent
collector rejections are retained in a dead-letter table; link and authentication
failures never discard queued evidence.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .schema import validate_rescue_event


OUTBOX_SCHEMA_VERSION = 1
MAX_RESPONSE_BYTES = 1 << 20


class RescueOutboxError(RuntimeError):
    """The local queue or remote delivery contract cannot be used safely."""


@dataclass(frozen=True)
class EnqueueResult:
    accepted: bool
    duplicate: bool
    event_id: str


@dataclass(frozen=True)
class FlushResult:
    delivered: int
    duplicates: int
    dead_lettered: int
    attempts: int
    pending: int
    stopped_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _canonical(event: Mapping[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _events_url(endpoint: str) -> str:
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise RescueOutboxError("collector endpoint must be non-empty")
    parsed = urlparse(endpoint.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RescueOutboxError("collector endpoint must be an http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RescueOutboxError(
            "collector endpoint must not contain credentials, query or fragment"
        )
    clean = endpoint.strip().rstrip("/")
    return clean if parsed.path.rstrip("/").endswith("/events") else f"{clean}/events"


def _decode_response(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RescueOutboxError("collector response exceeds size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RescueOutboxError("collector returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise RescueOutboxError("collector response must be a JSON object")
    return payload


def _post_event(
    url: str,
    canonical_json: str,
    *,
    token: str,
    timeout_s: float,
) -> tuple[int, dict[str, Any]]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "VeriSwarmRescueOutbox/1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        data=canonical_json.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            return response.status, _decode_response(raw)
    except HTTPError as error:
        raw = error.read(MAX_RESPONSE_BYTES + 1)
        try:
            payload = _decode_response(raw)
        except RescueOutboxError:
            payload = {"ok": False, "error": f"http_{error.code}"}
        return error.code, payload


class RescueOutbox:
    """SQLite-backed producer queue with exact-byte, FIFO replay semantics."""

    def __init__(self, mission_id: str, path: str | Path):
        self.mission_id = mission_id
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise RescueOutboxError(f"outbox_integrity_check_failed:{integrity}")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, OUTBOX_SCHEMA_VERSION}:
                raise RescueOutboxError(f"unsupported_outbox_schema:{version}")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS outbox (
                    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    canonical_json TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    enqueued_at_ms INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt_ms INTEGER,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS dead_letter (
                    dead_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    canonical_json TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    enqueued_at_ms INTEGER NOT NULL,
                    attempts INTEGER NOT NULL,
                    moved_at_ms INTEGER NOT NULL,
                    status_code INTEGER,
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS producer_sequence (
                    source TEXT PRIMARY KEY,
                    last_sequence INTEGER NOT NULL CHECK(last_sequence >= 0)
                );
                """
            )
            connection.execute(f"PRAGMA user_version={OUTBOX_SCHEMA_VERSION}")

    def reserve_source_sequence(self, source: str) -> int:
        """Atomically reserve the next durable sequence for one exact producer.

        Delivered outbox rows are deleted, so their sequence cannot be recovered by
        scanning the queue.  This retained counter prevents event-ID and source-sequence
        reuse when a producer process restarts with the same outbox.
        """

        if not isinstance(source, str) or not source.strip():
            raise RescueOutboxError("producer source must be non-empty")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT last_sequence FROM producer_sequence WHERE source = ?",
                (source,),
            ).fetchone()
            next_sequence = 1 if row is None else int(row["last_sequence"]) + 1
            connection.execute(
                """
                INSERT INTO producer_sequence (source, last_sequence) VALUES (?, ?)
                ON CONFLICT(source) DO UPDATE SET last_sequence = excluded.last_sequence
                """,
                (source, next_sequence),
            )
        return next_sequence

    def enqueue(self, candidate: Mapping[str, Any]) -> EnqueueResult:
        event = validate_rescue_event(candidate, expected_mission_id=self.mission_id)
        canonical = _canonical(event)
        content_hash = _sha256(canonical)
        event_id = event["event_id"]
        now_ms = time.time_ns() // 1_000_000
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT canonical_json FROM outbox WHERE event_id = ?", (event_id,)
            ).fetchone()
            if existing is not None:
                if existing["canonical_json"] != canonical:
                    raise RescueOutboxError("event_id_reused_with_different_content")
                return EnqueueResult(False, True, event_id)
            dead = connection.execute(
                "SELECT canonical_json FROM dead_letter WHERE event_id = ?", (event_id,)
            ).fetchone()
            if dead is not None:
                if dead["canonical_json"] != canonical:
                    raise RescueOutboxError("event_id_reused_with_different_content")
                raise RescueOutboxError("event_id_previously_dead_lettered")
            connection.execute(
                """
                INSERT INTO outbox (
                    event_id, canonical_json, content_sha256, enqueued_at_ms
                ) VALUES (?, ?, ?, ?)
                """,
                (event_id, canonical, content_hash, now_ms),
            )
        return EnqueueResult(True, False, event_id)

    def _pending_count(self, connection: sqlite3.Connection) -> int:
        return int(connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    def status(self, *, limit: int = 20) -> dict[str, Any]:
        if limit < 1:
            raise RescueOutboxError("status limit must be positive")
        with self._connect() as connection:
            pending = connection.execute(
                """
                SELECT event_id, enqueued_at_ms, attempts, last_attempt_ms, last_error
                FROM outbox ORDER BY queue_id LIMIT ?
                """,
                (limit,),
            ).fetchall()
            dead = connection.execute(
                """
                SELECT event_id, moved_at_ms, attempts, status_code, reason
                FROM dead_letter ORDER BY dead_id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return {
                "schema": "veriswarm.rescue.outbox.status.v1",
                "mission_id": self.mission_id,
                "path": str(self.path),
                "pending": self._pending_count(connection),
                "dead_lettered": int(
                    connection.execute("SELECT COUNT(*) FROM dead_letter").fetchone()[0]
                ),
                "pending_events": [dict(row) for row in pending],
                "dead_letters": [dict(row) for row in dead],
            }

    def _record_attempt(self, queue_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE outbox
                SET attempts = attempts + 1, last_attempt_ms = ?, last_error = NULL
                WHERE queue_id = ?
                """,
                (time.time_ns() // 1_000_000, queue_id),
            )

    def _record_last_error(self, queue_id: int, reason: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE outbox SET last_error = ? WHERE queue_id = ?",
                (reason[:512], queue_id),
            )

    def _delete_delivered(self, queue_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM outbox WHERE queue_id = ?", (queue_id,))

    def _move_to_dead_letter(
        self,
        queue_id: int,
        *,
        status_code: int | None,
        reason: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM outbox WHERE queue_id = ?", (queue_id,)
            ).fetchone()
            if row is None:
                connection.commit()
                return
            connection.execute(
                """
                INSERT INTO dead_letter (
                    event_id, canonical_json, content_sha256, enqueued_at_ms,
                    attempts, moved_at_ms, status_code, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["event_id"],
                    row["canonical_json"],
                    row["content_sha256"],
                    row["enqueued_at_ms"],
                    row["attempts"],
                    time.time_ns() // 1_000_000,
                    status_code,
                    reason[:512],
                ),
            )
            connection.execute("DELETE FROM outbox WHERE queue_id = ?", (queue_id,))
            connection.commit()

    def flush(
        self,
        endpoint: str,
        *,
        token: str = "",
        timeout_s: float = 3.0,
        max_events: int = 100,
        transient_retries: int = 2,
        retry_backoff_s: float = 0.25,
    ) -> FlushResult:
        """Deliver queued events and retain anything not conclusively accepted.

        Network, 4xx configuration and 5xx failures stop the current flush and leave the
        event queued. A 409 means the same identity/order cannot be accepted by that
        collector, so the event moves to the retained dead-letter table for inspection.
        """
        url = _events_url(endpoint)
        if timeout_s <= 0 or max_events < 1:
            raise RescueOutboxError("timeout and max_events must be positive")
        if transient_retries < 0 or transient_retries > 10:
            raise RescueOutboxError("transient_retries must be inside 0..10")
        if retry_backoff_s < 0 or retry_backoff_s > 30:
            raise RescueOutboxError("retry_backoff_s must be inside 0..30")

        delivered = duplicates = dead_lettered = attempts = 0
        stopped_reason: str | None = None
        with self._lock:
            while delivered + dead_lettered < max_events:
                with self._connect() as connection:
                    row = connection.execute(
                        "SELECT * FROM outbox ORDER BY queue_id LIMIT 1"
                    ).fetchone()
                if row is None:
                    break
                if _sha256(row["canonical_json"]) != row["content_sha256"]:
                    self._move_to_dead_letter(
                        row["queue_id"],
                        status_code=None,
                        reason="outbox_content_hash_mismatch",
                    )
                    dead_lettered += 1
                    continue

                event_complete = False
                for retry_index in range(transient_retries + 1):
                    attempts += 1
                    self._record_attempt(row["queue_id"])
                    try:
                        status_code, response = _post_event(
                            url,
                            row["canonical_json"],
                            token=token,
                            timeout_s=timeout_s,
                        )
                    except (OSError, TimeoutError, URLError) as error:
                        reason = f"network_error:{type(error).__name__}"
                        self._record_last_error(row["queue_id"], reason)
                        if retry_index < transient_retries:
                            time.sleep(retry_backoff_s * (2**retry_index))
                            continue
                        stopped_reason = reason
                        break
                    except RescueOutboxError as error:
                        reason = f"collector_protocol_error:{error}"
                        self._record_last_error(row["queue_id"], reason)
                        if retry_index < transient_retries:
                            time.sleep(retry_backoff_s * (2**retry_index))
                            continue
                        stopped_reason = reason
                        break

                    error_text = str(response.get("error", f"http_{status_code}"))
                    if status_code in {200, 202}:
                        if response.get("ok") is not True or response.get("accepted") is not True:
                            reason = "collector_protocol_error:acceptance_not_confirmed"
                            self._record_last_error(row["queue_id"], reason)
                            if retry_index < transient_retries:
                                time.sleep(retry_backoff_s * (2**retry_index))
                                continue
                            stopped_reason = reason
                            break
                        self._delete_delivered(row["queue_id"])
                        delivered += 1
                        duplicates += int(response.get("duplicate") is True)
                        event_complete = True
                        break
                    if status_code == 409:
                        self._record_last_error(row["queue_id"], error_text)
                        self._move_to_dead_letter(
                            row["queue_id"],
                            status_code=status_code,
                            reason=error_text,
                        )
                        dead_lettered += 1
                        event_complete = True
                        break

                    reason = f"http_{status_code}:{error_text}"
                    self._record_last_error(row["queue_id"], reason)
                    retryable = status_code in {408, 425, 429} or status_code >= 500
                    if retryable and retry_index < transient_retries:
                        time.sleep(retry_backoff_s * (2**retry_index))
                        continue
                    stopped_reason = reason
                    break
                if not event_complete:
                    break

        with self._connect() as connection:
            pending = self._pending_count(connection)
        return FlushResult(
            delivered=delivered,
            duplicates=duplicates,
            dead_lettered=dead_lettered,
            attempts=attempts,
            pending=pending,
            stopped_reason=stopped_reason,
        )
