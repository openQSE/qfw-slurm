"""Read-only QPM discovery and inspection through DEFw."""

from __future__ import annotations

from typing import Any

from .models import QPMAllocation, QPMService


class QPMInspectionError(RuntimeError):
    """Raised when the directory or QPM inspection contract fails."""


class QPMInspectionClient:
    """Discover QPMs and call only their read-only inspection bindings."""

    def __init__(self, directory: Any, defw_module: Any):
        self._directory = directory
        self._defw = defw_module

    @classmethod
    def connect(cls, timeout_seconds: float = 10.0) -> "QPMInspectionClient":
        try:
            import defw
            from defw_app_util import defw_get_directory_service

            directory = defw_get_directory_service(timeout=timeout_seconds)
        except Exception as error:
            raise QPMInspectionError(
                f"cannot connect to the DEFw directory service: {error}"
            ) from error
        return cls(directory, defw)

    def services(self) -> list[QPMService]:
        records = self._records("control")
        services = []
        for record in records:
            service = _service_record(record)
            service_id = str(service.get("service_id") or "")
            try:
                control = self._defw.connect_to_binding(record)
                summary = control.get_service_summary()
                services.append(_service(service_id, service, summary))
            except Exception as error:
                services.append(_unavailable_service(
                    service_id, service, str(error)
                ))
        return services

    def allocations(
        self, filters: dict[str, Any] | None = None
    ) -> list[QPMAllocation]:
        allocations = []
        for record in self._records("telemetry"):
            service = _service_record(record)
            service_id = str(service.get("service_id") or "")
            try:
                telemetry = self._defw.connect_to_binding(record)
                response = telemetry.list_scheduler_allocations(
                    filters=filters or {}
                )
            except Exception as error:
                raise QPMInspectionError(
                    f"cannot inspect QPM {service_id!r}: {error}"
                ) from error
            if not isinstance(response, dict) or not isinstance(
                response.get("allocations"), list
            ):
                raise QPMInspectionError(
                    f"QPM {service_id!r} returned malformed telemetry"
                )
            allocations.extend(
                _allocation(service_id, item)
                for item in response["allocations"]
            )
        return allocations

    def _records(self, binding_name: str) -> list[dict[str, Any]]:
        try:
            records = self._directory.resolve_services(
                service_type="qfw.qpm", binding_name=binding_name
            )
        except Exception as error:
            raise QPMInspectionError(
                f"QPM discovery failed: {error}"
            ) from error
        if not isinstance(records, list) or not all(
            isinstance(record, dict) for record in records
        ):
            raise QPMInspectionError(
                "directory returned malformed QPM binding records"
            )
        return records


def _service_record(record: dict[str, Any]) -> dict[str, Any]:
    service = record.get("service_record")
    if not isinstance(service, dict):
        raise QPMInspectionError("directory QPM result lacks service_record")
    return service


def _service(
    service_id: str,
    record: dict[str, Any],
    summary: Any,
) -> QPMService:
    if not service_id or not isinstance(summary, dict):
        raise QPMInspectionError("QPM returned a malformed service summary")
    if summary.get("schema") != "qfw-qpm-service-summary-v1":
        raise QPMInspectionError(
            f"QPM {service_id!r} returned an unsupported summary schema"
        )
    properties = record.get("properties") or {}
    selector = record.get("selector") or {}
    target = summary.get("target_id") or selector.get("name") or service_id
    return QPMService(
        service_id=service_id,
        runtime_id=str(record.get("runtime_id") or ""),
        generation=int(record.get("generation") or 0),
        backend=str(properties.get("provider") or target),
        state=str(summary.get("state") or "DOWN").upper(),
        ready=bool(summary.get("ready")),
        active_reservations=int(
            summary.get("active_reservation_count") or 0
        ),
        active_tasks=int(summary.get("active_task_count") or 0),
        assigned_hosts=tuple(
            str(host) for host in summary.get("assigned_hosts") or []
        ),
        dvm_ready=summary.get("dvm_ready"),
        raw=dict(summary),
    )


def _unavailable_service(
    service_id: str, record: dict[str, Any], error: str
) -> QPMService:
    properties = record.get("properties") or {}
    return QPMService(
        service_id=service_id,
        runtime_id=str(record.get("runtime_id") or ""),
        generation=int(record.get("generation") or 0),
        backend=str(properties.get("provider") or "unknown"),
        state="DOWN",
        ready=False,
        active_reservations=0,
        active_tasks=0,
        error=error,
    )


def _allocation(service_id: str, item: Any) -> QPMAllocation:
    if not isinstance(item, dict):
        raise QPMInspectionError(
            f"QPM {service_id!r} returned a malformed allocation"
        )
    return QPMAllocation(
        service_id=service_id,
        scheduler=str(item.get("scheduler") or ""),
        cluster_name=str(item.get("cluster_name") or ""),
        allocation_id=str(item.get("allocation_id") or ""),
        job_id=str(item.get("job_id") or ""),
        user=str(item.get("user") or ""),
        state=str(item.get("state") or "UNKNOWN").upper(),
        active_tasks=int(item.get("active_tasks") or 0),
        workload_kind=str(item.get("workload_kind") or ""),
        created_ns=item.get("created_ns"),
        updated_ns=item.get("updated_ns"),
    )
