"""Durable idempotency and reservation journal for the gateway."""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class JournalError(RuntimeError):
    """Raised when durable gateway state cannot be updated safely."""


class RequestConflict(JournalError):
    """Raised when an idempotency key is reused for another request."""


class AllocationNotFound(JournalError):
    """Raised when no journal record exists for an allocation."""


class AllocationNotAccepted(JournalError):
    """Raised when an allocation has no active accepted reservation set."""


class AllocationReleased(JournalError):
    """Raised when an allocation's reservation set has been released."""


@dataclasses.dataclass(frozen=True)
class OperationRecord:
    state: str
    response: dict[str, Any] | None


@dataclasses.dataclass(frozen=True)
class ReservationRecord:
    service_id: str
    reservation_id: int | None
    qpm_runtime_id: str | None
    qpm_generation: int | None
    state: str
    request_fingerprint: str | None = None


@dataclasses.dataclass(frozen=True)
class AllocationPlan:
    existing_response: dict[str, Any] | None
    existing_services: tuple[str, ...]
    missing_services: tuple[str, ...]

    @property
    def is_extension(self) -> bool:
        return self.existing_response is not None and bool(self.missing_services)


class Journal:
    """SQLite journal with transaction boundaries before every side effect."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        os.chmod(self.path, 0o600)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _initialize(self) -> None:
        schema = """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS operations (
            sender_uid INTEGER NOT NULL,
            operation TEXT NOT NULL,
            request_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            state TEXT NOT NULL,
            response_json TEXT,
            updated_ns TEXT NOT NULL,
            PRIMARY KEY (sender_uid, operation, request_id, scope)
        );
        CREATE TABLE IF NOT EXISTS allocations (
            cluster_name TEXT NOT NULL,
            canonical_job_id TEXT NOT NULL,
            job_uid INTEGER NOT NULL,
            job_gid INTEGER NOT NULL,
            service_set_json TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            state TEXT NOT NULL,
            response_json TEXT,
            updated_ns TEXT NOT NULL,
            PRIMARY KEY (cluster_name, canonical_job_id)
        );
        CREATE TABLE IF NOT EXISTS reservations (
            cluster_name TEXT NOT NULL,
            canonical_job_id TEXT NOT NULL,
            service_id TEXT NOT NULL,
            reservation_id TEXT,
            qpm_runtime_id TEXT,
            qpm_generation TEXT,
            request_fingerprint TEXT,
            state TEXT NOT NULL,
            diagnostic TEXT,
            updated_ns TEXT NOT NULL,
            PRIMARY KEY (cluster_name, canonical_job_id, service_id)
        );
        """
        with self._lock:
            self._connection.executescript(schema)

    def _now(self) -> str:
        return str(time.time_ns())

    def begin_operation(
        self,
        sender_uid: int,
        operation: str,
        request_id: int,
        fingerprint: str,
        scope: str = "allocation",
    ) -> OperationRecord | None:
        """Create an operation or return its durable prior outcome."""

        key = str(request_id)
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT fingerprint, state, response_json
                   FROM operations
                   WHERE sender_uid = ? AND operation = ? AND request_id = ?
                         AND scope = ?""",
                (sender_uid, operation, key, scope),
            ).fetchone()
            if row is not None:
                if row["fingerprint"] != fingerprint:
                    raise RequestConflict("request ID reused with different content")
                if row["state"] == "retryable":
                    self._connection.execute(
                        """UPDATE operations
                           SET state = 'started', response_json = NULL,
                               updated_ns = ?
                           WHERE sender_uid = ? AND operation = ?
                                 AND request_id = ? AND scope = ?""",
                        (
                            self._now(),
                            sender_uid,
                            operation,
                            key,
                            scope,
                        ),
                    )
                    return None
                response = (
                    json.loads(row["response_json"])
                    if row["response_json"] is not None
                    else None
                )
                return OperationRecord(row["state"], response)
            self._connection.execute(
                """INSERT INTO operations
                   (sender_uid, operation, request_id, scope, fingerprint,
                    state, response_json, updated_ns)
                   VALUES (?, ?, ?, ?, ?, 'started', NULL, ?)""",
                (sender_uid, operation, key, scope, fingerprint, self._now()),
            )
        return None

    def complete_operation(
        self,
        sender_uid: int,
        operation: str,
        request_id: int,
        response: dict[str, Any],
        scope: str = "allocation",
        state: str = "complete",
    ) -> None:
        if state not in {"complete", "retryable"}:
            raise JournalError("operation completion state is invalid")
        with self._lock, self._connection:
            changed = self._connection.execute(
                """UPDATE operations
                   SET state = ?, response_json = ?, updated_ns = ?
                   WHERE sender_uid = ? AND operation = ? AND request_id = ?
                         AND scope = ?""",
                (
                    state,
                    json.dumps(response, sort_keys=True, separators=(",", ":")),
                    self._now(),
                    sender_uid,
                    operation,
                    str(request_id),
                    scope,
                ),
            ).rowcount
            if changed != 1:
                raise JournalError("operation disappeared before completion")

    def begin_allocation(
        self,
        cluster_name: str,
        job_id: int,
        job_uid: int,
        job_gid: int,
        service_ids: tuple[str, ...],
        fingerprint: str,
        reservation_fingerprint: str,
    ) -> AllocationPlan:
        """Create an allocation transaction or plan an accepted extension."""

        key = str(job_id)
        requested = tuple(sorted(service_ids))
        service_json = json.dumps(requested, separators=(",", ":"))
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT job_uid, job_gid, service_set_json,
                          request_fingerprint, state, response_json
                   FROM allocations
                   WHERE cluster_name = ? AND canonical_job_id = ?""",
                (cluster_name, key),
            ).fetchone()
            if row is not None:
                same_identity = row["job_uid"] == job_uid and row["job_gid"] == job_gid
                if not same_identity:
                    raise RequestConflict("allocation identity changed")
                if row["state"] == "accepted" and row["response_json"]:
                    existing = tuple(json.loads(row["service_set_json"]))
                    existing_set = set(existing)
                    for service_id in set(requested) & existing_set:
                        reservation = self._connection.execute(
                            """SELECT state, request_fingerprint
                               FROM reservations
                               WHERE cluster_name = ?
                                     AND canonical_job_id = ?
                                     AND service_id = ?""",
                            (cluster_name, key, service_id),
                        ).fetchone()
                        if (
                            reservation is None
                            or reservation["state"] != "accepted"
                        ):
                            raise JournalError(
                                "accepted allocation lacks reservation state"
                            )
                        if (
                            reservation["request_fingerprint"]
                            != reservation_fingerprint
                        ):
                            raise RequestConflict(
                                f"accepted workload changed for {service_id!r}"
                            )
                    missing = tuple(
                        service_id
                        for service_id in requested
                        if service_id not in existing_set
                    )
                    response = json.loads(row["response_json"])
                    if not missing:
                        return AllocationPlan(response, existing, ())
                    combined = tuple(sorted(existing_set | set(missing)))
                    self._connection.execute(
                        """UPDATE allocations
                           SET service_set_json = ?, request_fingerprint = ?,
                               state = 'extending', updated_ns = ?
                           WHERE cluster_name = ? AND canonical_job_id = ?""",
                        (
                            json.dumps(combined, separators=(",", ":")),
                            fingerprint,
                            self._now(),
                            cluster_name,
                            key,
                        ),
                    )
                    return AllocationPlan(response, existing, missing)
                if row["state"] == "delayed":
                    self._connection.execute(
                        """UPDATE allocations
                           SET service_set_json = ?, request_fingerprint = ?,
                               state = 'reserving', response_json = NULL,
                               updated_ns = ?
                           WHERE cluster_name = ? AND canonical_job_id = ?""",
                        (
                            service_json,
                            fingerprint,
                            self._now(),
                            cluster_name,
                            key,
                        ),
                    )
                    return AllocationPlan(None, (), requested)
                if row["state"] in {
                    "reserving",
                    "rolling-back",
                    "extending",
                }:
                    raise JournalError("allocation has an incomplete transaction")
                if row["state"] == "released":
                    raise RequestConflict("allocation was already released")
                if row["request_fingerprint"] != fingerprint:
                    self._connection.execute(
                        """UPDATE allocations
                           SET service_set_json = ?, request_fingerprint = ?,
                               state = 'reserving', response_json = NULL,
                               updated_ns = ?
                           WHERE cluster_name = ? AND canonical_job_id = ?""",
                        (
                            service_json,
                            fingerprint,
                            self._now(),
                            cluster_name,
                            key,
                        )
                    )
                    return AllocationPlan(None, (), requested)
                response = (
                    json.loads(row["response_json"])
                    if row["response_json"]
                    else None
                )
                if response is None:
                    raise JournalError("allocation has no durable outcome")
                return AllocationPlan(response, (), ())
            self._connection.execute(
                """INSERT INTO allocations
                   (cluster_name, canonical_job_id, job_uid, job_gid,
                    service_set_json, request_fingerprint, state,
                    response_json, updated_ns)
                   VALUES (?, ?, ?, ?, ?, ?, 'reserving', NULL, ?)""",
                (
                    cluster_name,
                    key,
                    job_uid,
                    job_gid,
                    service_json,
                    fingerprint,
                    self._now(),
                ),
            )
        return AllocationPlan(None, (), requested)

    def accepted_allocation(
        self,
        cluster_name: str,
        job_id: int,
        job_uid: int,
        job_gid: int,
        service_ids: tuple[str, ...],
    ) -> dict[str, Any] | None:
        """Return an accepted set while processing an idempotent reserve."""

        service_json = json.dumps(sorted(service_ids), separators=(",", ":"))
        with self._lock:
            row = self._connection.execute(
                """SELECT job_uid, job_gid, service_set_json, state,
                          response_json
                   FROM allocations
                   WHERE cluster_name = ? AND canonical_job_id = ?""",
                (cluster_name, str(job_id)),
            ).fetchone()
        if row is None:
            return None
        stored_services = set(json.loads(row["service_set_json"]))
        requested_services = set(json.loads(service_json))
        if row["job_uid"] != job_uid or row["job_gid"] != job_gid:
            raise RequestConflict("allocation identity changed")
        if not requested_services.issubset(stored_services):
            raise RequestConflict("requested service is not reserved")
        if row["state"] != "accepted" or not row["response_json"]:
            return None
        return json.loads(row["response_json"])

    def record_reservation(
        self,
        cluster_name: str,
        job_id: int,
        record: ReservationRecord,
        diagnostic: str | None = None,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO reservations
                   (cluster_name, canonical_job_id, service_id,
                    reservation_id, qpm_runtime_id, qpm_generation,
                    request_fingerprint, state, diagnostic, updated_ns)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(cluster_name, canonical_job_id, service_id)
                   DO UPDATE SET reservation_id = excluded.reservation_id,
                       qpm_runtime_id = excluded.qpm_runtime_id,
                       qpm_generation = excluded.qpm_generation,
                       request_fingerprint = excluded.request_fingerprint,
                       state = excluded.state,
                       diagnostic = excluded.diagnostic,
                       updated_ns = excluded.updated_ns""",
                (
                    cluster_name,
                    str(job_id),
                    record.service_id,
                    str(record.reservation_id)
                    if record.reservation_id is not None
                    else None,
                    record.qpm_runtime_id,
                    str(record.qpm_generation)
                    if record.qpm_generation is not None
                    else None,
                    record.request_fingerprint,
                    record.state,
                    diagnostic,
                    self._now(),
                ),
            )

    def restore_accepted_allocation(
        self,
        cluster_name: str,
        job_id: int,
        service_ids: tuple[str, ...],
        response: dict[str, Any],
    ) -> None:
        """Restore a prior accepted set after an extension fails."""

        with self._lock, self._connection:
            changed = self._connection.execute(
                """UPDATE allocations
                   SET service_set_json = ?, state = 'accepted',
                       response_json = ?, updated_ns = ?
                   WHERE cluster_name = ? AND canonical_job_id = ?""",
                (
                    json.dumps(sorted(service_ids), separators=(",", ":")),
                    json.dumps(response, sort_keys=True, separators=(",", ":")),
                    self._now(),
                    cluster_name,
                    str(job_id),
                ),
            ).rowcount
            if changed != 1:
                raise JournalError("allocation disappeared during extension")

    def complete_allocation(
        self,
        cluster_name: str,
        job_id: int,
        state: str,
        response: dict[str, Any],
    ) -> None:
        with self._lock, self._connection:
            changed = self._connection.execute(
                """UPDATE allocations
                   SET state = ?, response_json = ?, updated_ns = ?
                   WHERE cluster_name = ? AND canonical_job_id = ?""",
                (
                    state,
                    json.dumps(response, sort_keys=True, separators=(",", ":")),
                    self._now(),
                    cluster_name,
                    str(job_id),
                ),
            ).rowcount
            if changed != 1:
                raise JournalError("allocation disappeared before completion")

    def reservations(
        self, cluster_name: str, job_id: int
    ) -> tuple[ReservationRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT service_id, reservation_id, qpm_runtime_id,
                          qpm_generation, state, request_fingerprint
                   FROM reservations
                   WHERE cluster_name = ? AND canonical_job_id = ?
                   ORDER BY service_id""",
                (cluster_name, str(job_id)),
            ).fetchall()
        return tuple(
            ReservationRecord(
                service_id=row["service_id"],
                reservation_id=int(row["reservation_id"])
                if row["reservation_id"] is not None
                else None,
                qpm_runtime_id=row["qpm_runtime_id"],
                qpm_generation=int(row["qpm_generation"])
                if row["qpm_generation"] is not None
                else None,
                state=row["state"],
                request_fingerprint=row["request_fingerprint"],
            )
            for row in rows
        )

    def reservation_context(
        self, cluster_name: str, job_id: int, job_uid: int, job_gid: int
    ) -> tuple[ReservationRecord, ...]:
        """Return the complete accepted reservation set without mutation."""

        with self._lock:
            allocation = self._connection.execute(
                """SELECT job_uid, job_gid, service_set_json, state
                   FROM allocations
                   WHERE cluster_name = ? AND canonical_job_id = ?""",
                (cluster_name, str(job_id)),
            ).fetchone()
            if allocation is None:
                raise AllocationNotFound("allocation is not in the journal")
            if allocation["job_uid"] != job_uid or allocation["job_gid"] != job_gid:
                raise RequestConflict("allocation identity changed")
            if allocation["state"] == "released":
                raise AllocationReleased("allocation reservations were released")
            if allocation["state"] != "accepted":
                raise AllocationNotAccepted(
                    f"allocation state is {allocation['state']!r}, not accepted"
                )
            try:
                expected = tuple(json.loads(allocation["service_set_json"]))
            except (TypeError, json.JSONDecodeError) as error:
                raise JournalError("allocation service set is malformed") from error
            all_records = self.reservations(cluster_name, job_id)
        if not expected or len(expected) != len(set(expected)):
            raise JournalError("allocation service set is malformed")
        expected_set = set(expected)
        records = tuple(
            item for item in all_records if item.service_id in expected_set
        )
        if any(
            item.state == "accepted" and item.service_id not in expected_set
            for item in all_records
        ):
            raise JournalError("journal contains an unexpected active reservation")
        if tuple(item.service_id for item in records) != tuple(sorted(expected)):
            raise JournalError("accepted allocation reservation set is incomplete")
        if any(item.state != "accepted" or item.reservation_id is None for item in records):
            raise JournalError("accepted allocation contains inactive reservations")
        return records

    def set_allocation_state(
        self, cluster_name: str, job_id: int, state: str
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE allocations SET state = ?, updated_ns = ?
                   WHERE cluster_name = ? AND canonical_job_id = ?""",
                (state, self._now(), cluster_name, str(job_id)),
            )

    def allocation_status(self, cluster_name: str, job_id: int) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM allocations
                   WHERE cluster_name = ? AND canonical_job_id = ?""",
                (cluster_name, str(job_id)),
            ).fetchone()
        if row is None:
            return {"state": "not-found", "reservations": []}
        result = dict(row)
        result["reservations"] = [
            dataclasses.asdict(item) for item in self.reservations(cluster_name, job_id)
        ]
        return result

    def nonterminal_allocations(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT cluster_name, canonical_job_id, job_uid, job_gid,
                          state, updated_ns
                   FROM allocations
                   WHERE state NOT IN ('released', 'rejected')
                   ORDER BY cluster_name, canonical_job_id"""
            ).fetchall()
        return [dict(row) for row in rows]
