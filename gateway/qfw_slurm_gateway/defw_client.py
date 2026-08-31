"""Narrow adapter from the gateway transaction model to QFw/DEFw."""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path
from typing import Any

from .protocol import (
    AdmissionDecision,
    ReserveRequest,
    ServiceResult,
    bounded_diagnostic,
)
from .slurm_verifier import VerifiedJob


class QFwAdapterError(RuntimeError):
    """Raised when discovery or a QPM operation fails."""


@dataclasses.dataclass(frozen=True)
class QPMBinding:
    service_id: str
    runtime_id: str
    generation: int
    admission: Any


class QFwAdapter:
    """Use current QFw APIs while keeping imports out of gateway startup tests."""

    def __init__(
        self,
        site_config: str,
        timeout_seconds: float = 10.0,
        activation: str | None = None,
        venv: str | None = None,
    ):
        self.site_config = Path(site_config)
        self.timeout_seconds = timeout_seconds
        self.activation = Path(activation) if activation else None
        self.venv = Path(venv) if venv else None
        self._resolver = None

    def start(self) -> None:
        if not self.site_config.is_file():
            raise QFwAdapterError(
                f"QFw site configuration does not exist: {self.site_config}"
            )
        if self.activation is not None and not self.activation.is_file():
            raise QFwAdapterError(
                f"QFw activation script does not exist: {self.activation}"
            )
        if (
            self.venv is not None
            and Path(sys.prefix).resolve() != self.venv.resolve()
        ):
            raise QFwAdapterError(
                f"gateway is using {sys.prefix}, expected QFw venv {self.venv}"
            )
        os.environ["QFW_SITE_CONFIG"] = str(self.site_config)
        try:
            import defw
            from defw_app_util import defw_get_directory_service
            from qfw_qiskit.qpm_resolver import QPMResolver
        except ImportError as error:
            raise QFwAdapterError(
                "QFw environment is not active for the gateway"
            ) from error
        try:
            directory = defw_get_directory_service(
                timeout=self.timeout_seconds
            )
            self._resolver = QPMResolver.from_environment(
                dirsvc=directory, defw_module=defw
            )
        except Exception as error:
            raise QFwAdapterError(
                f"cannot connect to the DEFw directory service: {error}"
            ) from error

    def resolve(self, service_id: str) -> QPMBinding:
        if self._resolver is None:
            raise QFwAdapterError("QFw adapter is not started")
        try:
            resolved, admission = self._resolver.resolve_and_connect(
                timeout=self.timeout_seconds,
                service_id=service_id,
                service_type="qfw.qpm",
                api_category="admission",
                binding_name="admission",
            )
            _control_resolved, control = self._resolver.resolve_and_connect(
                timeout=self.timeout_seconds,
                service_id=service_id,
                service_type="qfw.qpm",
                api_category="control",
                binding_name="control",
            )
            readiness = control.is_ready()
            if not isinstance(readiness, dict) or not readiness.get("ready"):
                raise QFwAdapterError(f"QPM {service_id!r} is not ready")
        except QFwAdapterError:
            raise
        except Exception as error:
            raise QFwAdapterError(
                f"cannot resolve QPM {service_id!r}: {error}"
            ) from error
        if resolved.runtime_id is None or resolved.generation is None:
            raise QFwAdapterError(
                f"QPM {service_id!r} lacks runtime identity or generation"
            )
        return QPMBinding(
            service_id=service_id,
            runtime_id=str(resolved.runtime_id),
            generation=int(resolved.generation),
            admission=admission,
        )

    def reserve(
        self,
        binding: QPMBinding,
        request: ReserveRequest,
        job: VerifiedJob,
    ) -> ServiceResult:
        workload = request.workload
        if workload is None:
            raise QFwAdapterError("cannot create a reservation without workload")
        scope = ":".join(
            value for value in (job.account, job.qos) if value
        ) or request.cluster_name
        payload = {
            "request_id": request.request_id,
            "owner": {"user": job.username, "uid": job.uid, "gid": job.gid},
            "job_id": str(request.canonical_job_id),
            "allocation_id": (
                f"{request.cluster_name}:{request.canonical_job_id}"
            ),
            "scope_id": scope,
            "account": job.account,
            "qos": job.qos,
            "priority": job.priority or 0,
            "workload_kind": workload.kind.name.lower(),
            "walltime_ns": workload.walltime_ns,
            "ttl_ns": workload.walltime_ns,
            "task_class": {
                "class_id": 1,
                "count": workload.circuit_count,
                "qubit_count": workload.max_qubits,
                "depth": workload.max_depth,
                "shots": workload.max_shots,
                "one_q_gate_count": workload.max_one_q_gates or 0,
                "two_q_gate_count": workload.max_two_q_gates or 0,
                "measurement_count": workload.max_measurements or 0,
            },
        }
        try:
            raw = binding.admission.reserve(request=payload)
        except Exception as error:
            raise QFwAdapterError(
                f"QPM {binding.service_id!r} reserve failed: {error}"
            ) from error
        try:
            return self._normalize_reserve(binding, raw)
        except QFwAdapterError as error:
            reservation_id = _accepted_reservation_id(raw)
            if reservation_id is not None:
                try:
                    self.release(binding, reservation_id, 1)
                except QFwAdapterError as cleanup_error:
                    raise QFwAdapterError(
                        f"{error}; malformed acceptance cleanup failed: "
                        f"{cleanup_error}"
                    ) from error
            raise

    def release(
        self, binding: QPMBinding, reservation_id: int, reason: int
    ) -> dict[str, Any]:
        try:
            result = binding.admission.release(
                reservation_id=reservation_id, reason=reason
            )
        except Exception as error:
            raise QFwAdapterError(
                f"QPM {binding.service_id!r} release failed: {error}"
            ) from error
        if not isinstance(result, dict):
            raise QFwAdapterError("QPM release returned a non-mapping")
        return result

    def _normalize_reserve(
        self, binding: QPMBinding, raw: Any
    ) -> ServiceResult:
        if not isinstance(raw, dict):
            raise QFwAdapterError("QPM reserve returned a non-mapping")
        status = str(raw.get("status", "")).lower()
        decisions = {
            "accepted": AdmissionDecision.ACCEPTED,
            "delayed": AdmissionDecision.DELAYED,
            "rejected": AdmissionDecision.REJECTED,
        }
        if status not in decisions:
            raise QFwAdapterError(f"QPM reserve returned invalid status {status!r}")
        reservation_id = raw.get("reservation_id")
        if reservation_id is not None:
            try:
                reservation_id = int(reservation_id)
            except (TypeError, ValueError) as error:
                raise QFwAdapterError(
                    "QPM reserve returned an invalid reservation ID"
                ) from error
        reason = raw.get("reason_code", 0)
        if (
            isinstance(reason, bool)
            or not isinstance(reason, int)
            or not 0 <= reason < 1 << 32
        ):
            raise QFwAdapterError("QPM reserve returned an invalid reason code")
        if decisions[status] == AdmissionDecision.ACCEPTED and (
            reservation_id is None
            or not 0 < reservation_id < 1 << 64
        ):
            raise QFwAdapterError(
                "accepted QPM reservation lacks a valid reservation ID"
            )
        diagnostic = raw.get("message") or raw.get("reason")
        return ServiceResult(
            service_id=binding.service_id,
            decision=decisions[status],
            reason_code=reason,
            reservation_id=reservation_id
            if decisions[status] == AdmissionDecision.ACCEPTED
            else None,
            retry_after_ns=_optional_int(raw.get("retry_after_ns")),
            estimated_start_ns=_optional_int(raw.get("estimated_start_ns")),
            estimated_finish_ns=_optional_int(raw.get("estimated_finish_ns")),
            qpm_runtime_id=binding.runtime_id,
            qpm_generation=binding.generation,
            diagnostic=bounded_diagnostic(diagnostic) if diagnostic else None,
        )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise QFwAdapterError("QPM reserve returned an invalid time value")
    try:
        selected = int(value)
    except (TypeError, ValueError) as error:
        raise QFwAdapterError(
            "QPM reserve returned an invalid time value"
        ) from error
    if not 0 <= selected < 1 << 64:
        raise QFwAdapterError("QPM reserve returned an invalid time value")
    return selected


def _accepted_reservation_id(value: Any) -> int | None:
    if (
        not isinstance(value, dict)
        or str(value.get("status", "")).lower() != "accepted"
    ):
        return None
    try:
        reservation_id = int(value.get("reservation_id"))
    except (TypeError, ValueError):
        return None
    return reservation_id if 0 < reservation_id < 1 << 64 else None
