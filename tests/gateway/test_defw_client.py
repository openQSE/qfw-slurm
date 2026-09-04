from __future__ import annotations

import sys

import pytest

from qfw_slurm_gateway.defw_client import (
    QFwAdapter,
    QFwAdapterError,
    QPMBinding,
)
from qfw_slurm_gateway.protocol import (
    AdmissionDecision,
    EvaluateRequest,
    ReserveRequest,
    Workload,
    WorkloadKind,
)
from qfw_slurm_gateway.slurm_verifier import VerifiedJob


def test_adapter_rejects_wrong_python_environment(tmp_path) -> None:
    site = tmp_path / "site.yaml"
    activation = tmp_path / "qfw-activate"
    site.write_text("services: []\n", encoding="utf-8")
    activation.write_text("", encoding="utf-8")
    adapter = QFwAdapter(
        str(site),
        activation=str(activation),
        venv=str(tmp_path / "another-venv"),
    )
    with pytest.raises(QFwAdapterError, match="expected QFw venv"):
        adapter.start()


def test_adapter_requires_site_configuration(tmp_path) -> None:
    adapter = QFwAdapter(str(tmp_path / "missing.yaml"), venv=sys.prefix)
    with pytest.raises(QFwAdapterError, match="does not exist"):
        adapter.start()


class Admission:
    def __init__(self, result):
        self.result = result
        self.request = None
        self.releases = []

    def reserve(self, request):
        self.request = request
        return self.result

    def evaluate(self, request):
        self.request = request
        return self.result

    def release(self, reservation_id, reason):
        self.releases.append((reservation_id, reason))
        return {"status": "released"}


def test_reserve_maps_trusted_slurm_metadata() -> None:
    admission = Admission({"status": "accepted", "reservation_id": "41"})
    binding = QPMBinding("nwqsim", "runtime", 1, admission)
    request = ReserveRequest(
        7,
        "cluster",
        42,
        1001,
        1002,
        Workload(WorkloadKind.QUANTUM, 10, 1, 2, 3, 4),
        ("nwqsim",),
    )
    job = VerifiedJob(
        "cluster", 42, 1001, 1002, "user-a", "project", "normal", 17,
        "RUNNING",
    )

    result = QFwAdapter("/site.yaml").reserve(binding, request, job)

    assert result.reservation_id == 41
    assert admission.request["scope_id"] == "project:normal"
    assert admission.request["priority"] == 17
    assert admission.request["owner"]["user"] == "user-a"
    assert admission.request["scheduler"] == "slurm"
    assert admission.request["launcher"] == {
        "scheduler": "slurm",
        "cluster_name": "cluster",
    }


def test_evaluate_maps_metadata_without_reserving() -> None:
    admission = Admission({"status": "accepted"})
    binding = QPMBinding("nwqsim", "runtime", 1, admission)
    request = EvaluateRequest(
        7,
        "cluster",
        42,
        1001,
        1002,
        Workload(WorkloadKind.QUANTUM, 10, 1, 2, 3, 4),
        ("nwqsim",),
    )
    job = VerifiedJob(
        "cluster", 42, 1001, 1002, "user-a", "project", "normal", 17,
        "PENDING",
    )

    result = QFwAdapter("/site.yaml").evaluate(binding, request, job)

    assert result.decision == AdmissionDecision.ACCEPTED
    assert result.reservation_id is None
    assert admission.request["scope_id"] == "project:normal"


def test_evaluate_accepts_zero_reservation_sentinel() -> None:
    admission = Admission({"status": "accepted", "reservation_id": 0})
    binding = QPMBinding("nwqsim", "runtime", 1, admission)
    request = EvaluateRequest(
        7,
        "cluster",
        42,
        1001,
        1002,
        Workload(WorkloadKind.QUANTUM, 10, 1, 2, 3, 4),
        ("nwqsim",),
    )
    job = VerifiedJob(
        "cluster", 42, 1001, 1002, "user-a", None, None, None, "PENDING"
    )

    result = QFwAdapter("/site.yaml").evaluate(binding, request, job)

    assert result.decision == AdmissionDecision.ACCEPTED
    assert result.reservation_id is None


def test_evaluate_rejects_reservation_id() -> None:
    admission = Admission({"status": "accepted", "reservation_id": 41})
    binding = QPMBinding("nwqsim", "runtime", 1, admission)
    request = EvaluateRequest(
        7,
        "cluster",
        42,
        1001,
        1002,
        Workload(WorkloadKind.QUANTUM, 10, 1, 2, 3, 4),
        ("nwqsim",),
    )
    job = VerifiedJob(
        "cluster", 42, 1001, 1002, "user-a", None, None, None, "PENDING"
    )

    with pytest.raises(QFwAdapterError, match="returned a reservation ID"):
        QFwAdapter("/site.yaml").evaluate(binding, request, job)


def test_accepted_reservation_requires_nonzero_id() -> None:
    admission = Admission({"status": "accepted", "reservation_id": 0})
    binding = QPMBinding("nwqsim", "runtime", 1, admission)
    request = ReserveRequest(
        7,
        "cluster",
        42,
        1001,
        1002,
        Workload(WorkloadKind.QUANTUM, 10, 1, 2, 3, 4),
        ("nwqsim",),
    )
    job = VerifiedJob(
        "cluster", 42, 1001, 1002, "user-a", None, None, None, "RUNNING"
    )

    with pytest.raises(QFwAdapterError, match="valid reservation ID"):
        QFwAdapter("/site.yaml").reserve(binding, request, job)
    assert admission.releases == []


def test_malformed_accepted_result_is_released() -> None:
    admission = Admission(
        {"status": "accepted", "reservation_id": 41, "reason_code": "bad"}
    )
    binding = QPMBinding("nwqsim", "runtime", 1, admission)
    request = ReserveRequest(
        7,
        "cluster",
        42,
        1001,
        1002,
        Workload(WorkloadKind.QUANTUM, 10, 1, 2, 3, 4),
        ("nwqsim",),
    )
    job = VerifiedJob(
        "cluster", 42, 1001, 1002, "user-a", None, None, None, "RUNNING"
    )

    with pytest.raises(QFwAdapterError, match="invalid reason code"):
        QFwAdapter("/site.yaml").reserve(binding, request, job)
    assert admission.releases == [(41, 1)]
