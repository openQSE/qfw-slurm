from __future__ import annotations

import pytest

from qfw_slurm_inspect.qpm import QPMInspectionClient, QPMInspectionError


def _record(binding="control"):
    return {
        "service_record": {
            "service_id": "nwqsim",
            "runtime_id": "runtime-a",
            "generation": 3,
            "properties": {"provider": "nwqsim"},
        },
        "selected_binding": {"binding_name": binding},
    }


class Directory:
    def resolve_services(self, **filters):
        return [_record(filters["binding_name"])]


class Control:
    def get_service_summary(self):
        return {
            "schema": "qfw-qpm-service-summary-v1",
            "state": "BUSY",
            "ready": True,
            "target_id": "nwqsim",
            "active_reservation_count": 2,
            "active_task_count": 1,
            "assigned_hosts": ["nwqsim-head", "nwqsim-worker-1"],
        }


class Telemetry:
    def list_scheduler_allocations(self, filters=None):
        assert filters == {"scheduler": "slurm"}
        return {
            "schema": "qfw-scheduler-allocation-list-v1",
            "allocations": [{
                "scheduler": "slurm",
                "cluster_name": "qfw-slurm",
                "allocation_id": "qfw-slurm:42",
                "job_id": "42",
                "user": "user-a",
                "state": "ACTIVE",
                "active_tasks": 1,
            }],
        }


class DEFw:
    def connect_to_binding(self, record):
        binding = record["selected_binding"]["binding_name"]
        return Control() if binding == "control" else Telemetry()


def test_reads_sanitized_service_and_allocation_contracts() -> None:
    client = QPMInspectionClient(Directory(), DEFw())

    service = client.services()[0]
    allocations = client.allocations({"scheduler": "slurm"})

    assert service.service_id == "nwqsim"
    assert service.runtime_id == "runtime-a"
    assert service.generation == 3
    assert service.state == "BUSY"
    assert service.active_reservations == 2
    assert allocations[0].allocation_id == "qfw-slurm:42"
    assert not hasattr(allocations[0], "reservation_id")


def test_rejects_malformed_directory_results() -> None:
    class BadDirectory:
        def resolve_services(self, **filters):
            return {"not": "a list"}

    client = QPMInspectionClient(BadDirectory(), DEFw())

    with pytest.raises(QPMInspectionError, match="malformed"):
        client.services()
