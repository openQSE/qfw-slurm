"""Independent verification of Slurm-owned allocation identity."""

from __future__ import annotations

import dataclasses
import json
import pwd
import subprocess
from collections.abc import Callable
from typing import Any

from .protocol import GetReservationsRequest, ReleaseRequest, ReserveRequest


class SlurmVerificationError(RuntimeError):
    """Raised when request identity does not match Slurm controller state."""


@dataclasses.dataclass(frozen=True)
class VerifiedJob:
    cluster_name: str
    canonical_job_id: int
    uid: int
    gid: int
    username: str
    account: str | None
    qos: str | None
    priority: int | None
    state: str


def _number(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise SlurmVerificationError(f"Slurm {label} is invalid")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    if isinstance(value, dict):
        for key in ("number", "set", "value", "id"):
            if key in value and value[key] is not None:
                return _number(value[key], label)
    raise SlurmVerificationError(f"Slurm {label} is unavailable")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("name", "string", "value"):
            if key in value:
                return _text(value[key])
        return None
    if isinstance(value, list):
        return _text(value[0]) if value else None
    return str(value)


class SlurmVerifier:
    """Query slurmctld instead of trusting fields supplied by the plugin."""

    _reserve_states = {"PENDING", "CONFIGURING", "RUNNING", "COMPLETING"}
    _release_states = _reserve_states | {
        "CANCELLED",
        "COMPLETED",
        "FAILED",
        "NODE_FAIL",
        "OUT_OF_MEMORY",
        "PREEMPTED",
        "TIMEOUT",
    }

    def __init__(
        self,
        cluster_name: str,
        command: str = "scontrol",
        timeout_seconds: float = 5.0,
        trusted_sender_uids: frozenset[int] = frozenset(),
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ):
        self.cluster_name = cluster_name
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.trusted_sender_uids = trusted_sender_uids
        self._runner = runner

    def _sender_is_trusted(self, sender_uid: int, job_uid: int) -> bool:
        return sender_uid in {0, job_uid} or sender_uid in self.trusted_sender_uids

    def verify_reserve(
        self, request: ReserveRequest, sender_uid: int
    ) -> VerifiedJob:
        if request.cluster_name != self.cluster_name:
            raise SlurmVerificationError("request names another Slurm cluster")
        job = self._load(
            request.canonical_job_id,
            request.hetero_job_id,
            request.hetero_component,
        )
        if job.uid != request.job_uid or job.gid != request.job_gid:
            raise SlurmVerificationError("request identity differs from Slurm")
        if not self._sender_is_trusted(sender_uid, job.uid):
            raise SlurmVerificationError("MUNGE identity cannot reserve this job")
        if job.state not in self._reserve_states:
            raise SlurmVerificationError(
                f"Slurm job state {job.state!r} cannot reserve QPMs"
            )
        return dataclasses.replace(job, cluster_name=self.cluster_name)

    def verify_release(
        self,
        request: ReleaseRequest,
        sender_uid: int,
        expected_uid: int | None,
    ) -> VerifiedJob:
        if request.cluster_name != self.cluster_name:
            raise SlurmVerificationError("request names another Slurm cluster")
        job = self._load(request.canonical_job_id)
        if expected_uid is not None and job.uid != expected_uid:
            raise SlurmVerificationError("journal and Slurm owner differ")
        if not self._sender_is_trusted(sender_uid, job.uid):
            raise SlurmVerificationError("MUNGE identity cannot release this job")
        if job.state not in self._release_states:
            raise SlurmVerificationError(
                f"Slurm job state {job.state!r} cannot release QPMs"
            )
        return job

    def verify_lookup(
        self, request: GetReservationsRequest, sender_uid: int
    ) -> VerifiedJob:
        if request.cluster_name != self.cluster_name:
            raise SlurmVerificationError("request names another Slurm cluster")
        jobs, observed = self._query(request.observed_job_id)
        canonical_job_id = self._canonical_job_id(observed)
        if canonical_job_id == request.observed_job_id:
            canonical = observed
        else:
            canonical = next(
                (
                    item
                    for item in jobs
                    if _number(item.get("job_id"), "job ID")
                    == canonical_job_id
                ),
                None,
            )
            if canonical is None:
                _jobs, canonical = self._query(canonical_job_id)
        job = self._verified_job(canonical, canonical_job_id)
        observed_uid = _number(
            observed.get("user_id", observed.get("user_id_number")), "UID"
        )
        observed_gid = _number(
            observed.get("group_id", observed.get("group_id_number")), "GID"
        )
        if (observed_uid, observed_gid) != (job.uid, job.gid):
            raise SlurmVerificationError(
                "heterogeneous component owner differs from allocation"
            )
        if job.uid != request.job_uid or job.gid != request.job_gid:
            raise SlurmVerificationError("request identity differs from Slurm")
        if not self._sender_is_trusted(sender_uid, job.uid):
            raise SlurmVerificationError(
                "MUNGE identity cannot retrieve reservations for this job"
            )
        if job.state not in self._reserve_states:
            raise SlurmVerificationError(
                f"Slurm job state {job.state!r} has no active reservation context"
            )
        return dataclasses.replace(job, cluster_name=self.cluster_name)

    @staticmethod
    def _canonical_job_id(record: dict[str, Any]) -> int:
        value = record.get("het_job_id")
        if value is None:
            return _number(record.get("job_id"), "job ID")
        try:
            candidate = _number(value, "heterogeneous job ID")
        except SlurmVerificationError:
            return _number(record.get("job_id"), "job ID")
        if candidate in {0, 4294967294, 4294967295}:
            return _number(record.get("job_id"), "job ID")
        return candidate

    def _query(self, job_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            result = self._runner(
                [self.command, "--json", "show", "job", str(job_id)],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SlurmVerificationError(f"cannot query slurmctld: {error}") from error
        if result.returncode != 0:
            detail = result.stderr.strip() or "scontrol failed"
            raise SlurmVerificationError(detail)
        try:
            document = json.loads(result.stdout)
            jobs = document["jobs"]
            record = next(
                item
                for item in jobs
                if _number(item.get("job_id"), "job ID") == job_id
            )
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise SlurmVerificationError("scontrol returned invalid JSON") from error
        except StopIteration as error:
            raise SlurmVerificationError(
                "scontrol response lacks the requested job"
            ) from error
        return jobs, record

    @staticmethod
    def _verified_job(record: dict[str, Any], job_id: int) -> VerifiedJob:
        uid = _number(record.get("user_id", record.get("user_id_number")), "UID")
        gid = _number(record.get("group_id", record.get("group_id_number")), "GID")
        state = (_text(record.get("job_state", record.get("state"))) or "").upper()
        if "+" in state:
            state = state.split("+", 1)[0]
        try:
            username = pwd.getpwuid(uid).pw_name
        except KeyError:
            username = str(uid)
        return VerifiedJob(
            cluster_name="",
            canonical_job_id=job_id,
            uid=uid,
            gid=gid,
            username=username,
            account=_text(record.get("account")),
            qos=_text(record.get("qos")),
            priority=_number(record["priority"], "priority")
            if record.get("priority") is not None
            else None,
            state=state,
        )

    def _load(
        self,
        job_id: int,
        hetero_job_id: int | None = None,
        hetero_component: int | None = None,
    ) -> VerifiedJob:
        jobs, record = self._query(job_id)
        if (hetero_job_id is None) != (hetero_component is None):
            raise SlurmVerificationError(
                "heterogeneous request metadata is incomplete"
            )
        if hetero_job_id is not None:
            self._verify_heterogeneous_component(
                jobs, job_id, hetero_job_id, hetero_component
            )
        return dataclasses.replace(
            self._verified_job(record, job_id), cluster_name=self.cluster_name
        )

    def _verify_heterogeneous_component(
        self,
        jobs: list[dict[str, Any]],
        canonical_job_id: int,
        component_job_id: int,
        component_offset: int,
    ) -> None:
        try:
            component = next(
                item
                for item in jobs
                if _number(item.get("job_id"), "job ID") == component_job_id
            )
        except StopIteration as error:
            raise SlurmVerificationError(
                "scontrol response lacks the heterogeneous component"
            ) from error
        leader = _number(component.get("het_job_id"), "heterogeneous job ID")
        offset = _number(
            component.get("het_job_offset"), "heterogeneous component"
        )
        if leader != canonical_job_id or offset != component_offset:
            raise SlurmVerificationError(
                "heterogeneous component differs from Slurm"
            )
