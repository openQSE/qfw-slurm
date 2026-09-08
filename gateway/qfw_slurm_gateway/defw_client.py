"""Narrow adapter from the gateway transaction model to QFw/DEFw."""

from __future__ import annotations

import dataclasses
import os
import sys
import threading
from pathlib import Path
from typing import Any

from .protocol import (
    AdmissionDecision,
    EvaluateRequest,
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
        self._defw = None
        self._directory = None
        self._directory_getter = None
        self._binding_factory = None
        self._bindings: dict[str, Any] = {}
        self._bindings_lock = threading.Lock()

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
            from api_qpm_common import QPMLifecycleBinding
            from defw_app_util import defw_get_directory_service
        except ImportError as error:
            raise QFwAdapterError(
                "QFw environment is not active for the gateway"
            ) from error
        try:
            directory = defw_get_directory_service(
                timeout=self.timeout_seconds
            )
            self._defw = defw
            self._directory = directory
            self._directory_getter = lambda: getattr(defw, "dirsvc", None)
            self._binding_factory = QPMLifecycleBinding
        except Exception as error:
            raise QFwAdapterError(
                f"cannot connect to the DEFw directory service: {error}"
            ) from error

    def resolve(self, service_id: str) -> QPMBinding:
        if self._binding_factory is None:
            raise QFwAdapterError("QFw adapter is not started")
        try:
            lifecycle = self._managed_binding(service_id)
            identity = lifecycle.snapshot()
            if not identity.get("available") or not identity.get("runtime_id"):
                raise QFwAdapterError(f"QPM {service_id!r} is unavailable")
            runtime_id = str(identity["runtime_id"])
            control = lifecycle.api(
                "control", expected_runtime_id=runtime_id
            )
            readiness = control.is_ready()
            if not isinstance(readiness, dict) or not readiness.get("ready"):
                raise QFwAdapterError(f"QPM {service_id!r} is not ready")
            current = lifecycle.snapshot()
            if (
                not current.get("available")
                or str(current.get("runtime_id")) != runtime_id
            ):
                raise QFwAdapterError(
                    f"QPM {service_id!r} changed while checking readiness"
                )
            admission = lifecycle.api(
                "admission", expected_runtime_id=runtime_id
            )
        except QFwAdapterError:
            raise
        except Exception as error:
            raise QFwAdapterError(
                f"cannot resolve QPM {service_id!r}: {error}"
            ) from error
        if current.get("generation") is None:
            raise QFwAdapterError(
                f"QPM {service_id!r} lacks runtime identity or generation"
            )
        return QPMBinding(
            service_id=service_id,
            runtime_id=runtime_id,
            generation=int(current["generation"]),
            admission=admission,
        )

    def close(self) -> None:
        with self._bindings_lock:
            bindings = list(self._bindings.values())
            self._bindings.clear()
        for binding in bindings:
            binding.close()

    def _managed_binding(self, service_id: str):
        with self._bindings_lock:
            binding = self._bindings.get(service_id)
            if binding is not None:
                return binding
            binding = self._binding_factory(
                service_id,
                directory_getter=self._directory_getter,
                defw_module=self._defw,
                recovery_timeout=self.timeout_seconds,
            )
            binding.start(directory=self._directory)
            self._bindings[service_id] = binding
            return binding

    def reserve(
        self,
        binding: QPMBinding,
        request: ReserveRequest,
        job: VerifiedJob,
    ) -> ServiceResult:
        payload = self._admission_payload(request, job)
        try:
            raw = binding.admission.reserve(request=payload)
        except Exception as error:
            raise QFwAdapterError(
                f"QPM {binding.service_id!r} reserve failed: {error}"
            ) from error
        try:
            return self._normalize_admission(binding, raw, reserve=True)
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

    def evaluate(
        self,
        binding: QPMBinding,
        request: EvaluateRequest,
        job: VerifiedJob,
    ) -> ServiceResult:
        payload = self._admission_payload(request, job)
        try:
            raw = binding.admission.evaluate(request=payload)
        except Exception as error:
            raise QFwAdapterError(
                f"QPM {binding.service_id!r} evaluate failed: {error}"
            ) from error
        return self._normalize_admission(binding, raw, reserve=False)

    def _admission_payload(
        self, request: ReserveRequest, job: VerifiedJob
    ) -> dict[str, Any]:
        workload = request.workload
        if workload is None:
            raise QFwAdapterError("admission request lacks a workload")
        scope = ":".join(
            value for value in (job.account, job.qos) if value
        ) or request.cluster_name
        return {
            "request_id": request.request_id,
            "owner": {"user": job.username, "uid": job.uid, "gid": job.gid},
            "scheduler": "slurm",
            "launcher": {
                "scheduler": "slurm",
                "cluster_name": request.cluster_name,
            },
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

    def _normalize_admission(
        self, binding: QPMBinding, raw: Any, reserve: bool
    ) -> ServiceResult:
        if not isinstance(raw, dict):
            raise QFwAdapterError("QPM admission returned a non-mapping")
        status = str(raw.get("status", "")).lower()
        decisions = {
            "accepted": AdmissionDecision.ACCEPTED,
            "delayed": AdmissionDecision.DELAYED,
            "rejected": AdmissionDecision.REJECTED,
        }
        if status not in decisions:
            raise QFwAdapterError(
                f"QPM admission returned invalid status {status!r}"
            )
        reservation_id = raw.get("reservation_id")
        if reservation_id in (0, "0"):
            reservation_id = None
        if reservation_id is not None:
            try:
                reservation_id = int(reservation_id)
            except (TypeError, ValueError) as error:
                raise QFwAdapterError(
                    "QPM admission returned an invalid reservation ID"
                ) from error
        reason = raw.get("reason_code", 0)
        if (
            isinstance(reason, bool)
            or not isinstance(reason, int)
            or not 0 <= reason < 1 << 32
        ):
            raise QFwAdapterError("QPM admission returned an invalid reason code")
        if reserve and decisions[status] == AdmissionDecision.ACCEPTED and (
            reservation_id is None
            or not 0 < reservation_id < 1 << 64
        ):
            raise QFwAdapterError(
                "accepted QPM reservation lacks a valid reservation ID"
            )
        if not reserve and reservation_id is not None:
            raise QFwAdapterError("QPM evaluation returned a reservation ID")
        diagnostic = raw.get("message") or raw.get("reason")
        return ServiceResult(
            service_id=binding.service_id,
            decision=decisions[status],
            reason_code=reason,
            reservation_id=reservation_id
            if reserve and decisions[status] == AdmissionDecision.ACCEPTED
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
