from __future__ import annotations

import json

from qfw_slurm_inspect.models import QPMAllocation, SlurmJob
from qfw_slurm_inspect.squeue import canonical_job_id, correlate, main


def allocation(service="nwqsim", job="42", user="user-a"):
    return QPMAllocation(
        service, "slurm", "qfw-cluster", f"qfw-cluster:{job}", job,
        user, "ACTIVE", 0,
    )


def test_correlates_ordinary_job_without_exposing_reservation_id() -> None:
    jobs = [SlurmJob("42", "user-a", "RUNNING", ("c1",), "qfw-cluster")]

    row = correlate(jobs, [allocation()])[0]

    assert row["qpu"] == "nwqsim"
    assert row["qstate"] == "ACTIVE"
    assert "reservation" not in str(row).lower()


def test_combines_heterogeneous_components_and_multiple_qpms() -> None:
    jobs = [
        SlurmJob("42", "user-a", "RUNNING", ("c1",), "qfw-cluster", "42", 0),
        SlurmJob("43", "user-a", "RUNNING", ("c2",), "qfw-cluster", "42", 1),
    ]

    rows = correlate(jobs, [allocation(), allocation("iqm-ornl-20q")])

    assert len(rows) == 1
    assert rows[0]["job_id"] == "42"
    assert rows[0]["components"] == ["42", "43"]
    assert rows[0]["nodes"] == "c1,c2"
    assert rows[0]["qpu"] == "iqm-ornl-20q,nwqsim"


def test_pending_and_completing_quantum_states() -> None:
    pending = SlurmJob("40", "user-a", "PENDING", (), "qfw-cluster")
    completing = SlurmJob("42", "user-a", "COMPLETING", ("c1",), "qfw-cluster")

    assert correlate([pending], [])[0]["qstate"] == "EVALUATING"
    assert correlate([pending], [allocation(job="40")])[0]["qstate"] == "ACCEPTED"
    assert correlate([completing], [allocation()])[0]["qstate"] == "RELEASING"


def test_reports_qpm_rejection_and_unavailable_service() -> None:
    running = SlurmJob("42", "user-a", "RUNNING", ("c1",), "qfw-cluster")
    rejected = QPMAllocation(
        "nwqsim", "slurm", "qfw-cluster", "qfw-cluster:42", "42",
        "user-a", "REJECTED", 0,
    )

    assert correlate([running], [rejected])[0]["qstate"] == "REJECTED"
    assert correlate([running], [])[0]["qstate"] == "UNAVAILABLE"


def test_main_filters_by_user_and_job(monkeypatch, capsys) -> None:
    jobs = [
        SlurmJob("41", "user-b", "RUNNING", ("c1",), "qfw-cluster"),
        SlurmJob("42", "user-a", "RUNNING", ("c2",), "qfw-cluster"),
    ]
    monkeypatch.setattr(
        "qfw_slurm_inspect.squeue.SlurmJsonClient.jobs",
        lambda self: jobs,
    )
    monkeypatch.setattr(
        "qfw_slurm_inspect.squeue.QPMInspectionClient.connect",
        lambda timeout: type("Client", (), {
            "errors": [],
            "allocations": lambda self, filters: [allocation()]
        })(),
    )

    assert main(["--json", "--user", "user-a", "--job", "42"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "qfw-squeue-v1"
    assert [job["job_id"] for job in payload["jobs"]] == ["42"]


def test_canonical_job_id_defaults_to_component_job() -> None:
    assert canonical_job_id(SlurmJob("9", "user", "RUNNING")) == "9"
