from __future__ import annotations

import pytest

from qfw_slurm_gateway.journal import (
    AllocationNotAccepted,
    AllocationNotFound,
    Journal,
    JournalError,
    RequestConflict,
    ReservationRecord,
)


def test_operation_replay_and_conflict(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    assert journal.begin_operation(0, "reserve", 7, "abc") is None
    journal.complete_operation(0, "reserve", 7, {"result": "yes"})
    replay = journal.begin_operation(0, "reserve", 7, "abc")
    assert replay is not None
    assert replay.response == {"result": "yes"}
    with pytest.raises(RequestConflict):
        journal.begin_operation(0, "reserve", 7, "different")
    assert journal.begin_operation(
        0, "reserve", 7, "different", "heterogeneous:7:1"
    ) is None
    journal.close()


def test_retryable_operation_starts_again(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    assert journal.begin_operation(0, "evaluate", 7, "abc") is None
    journal.complete_operation(
        0,
        "evaluate",
        7,
        {"decision": "delayed"},
        state="retryable",
    )
    assert journal.begin_operation(0, "evaluate", 7, "abc") is None
    journal.complete_operation(0, "evaluate", 7, {"decision": "accepted"})
    replay = journal.begin_operation(0, "evaluate", 7, "abc")
    assert replay is not None
    assert replay.response == {"decision": "accepted"}
    journal.close()


def test_rolled_back_allocation_reserves_again(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    journal.begin_allocation(
        "cluster", 42, 1000, 1000, ("svc",), "x", "workload"
    )
    journal.set_allocation_state("cluster", 42, "rolling-back")
    with pytest.raises(JournalError, match="incomplete transaction"):
        journal.begin_allocation(
            "cluster", 42, 1000, 1000, ("svc",), "x", "workload"
        )
    journal.complete_allocation("cluster", 42, "rolled-back", {"error_code": 4})

    plan = journal.begin_allocation(
        "cluster", 42, 1000, 1000, ("svc", "other"), "y", "workload"
    )

    assert plan.existing_response is None
    assert plan.existing_services == ()
    assert plan.missing_services == ("other", "svc")
    status = journal.allocation_status("cluster", 42)
    assert status["state"] == "reserving"
    assert status["response_json"] is None
    journal.close()


def test_uint64_values_are_stored_as_decimal_text(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    largest = (1 << 64) - 1
    journal.begin_allocation(
        "cluster", largest, 1000, 1000, ("svc",), "x", "workload"
    )
    journal.record_reservation(
        "cluster",
        largest,
        ReservationRecord(
            "svc", largest, "runtime", largest, "accepted", "workload"
        ),
    )
    record = journal.reservations("cluster", largest)[0]
    assert record.reservation_id == largest
    assert record.qpm_generation == largest
    journal.close()


def test_reservation_context_requires_complete_accepted_set(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    with pytest.raises(AllocationNotFound):
        journal.reservation_context("cluster", 42, 1000, 1000)
    journal.begin_allocation(
        "cluster", 42, 1000, 1000, ("svc",), "x", "workload"
    )
    with pytest.raises(AllocationNotAccepted):
        journal.reservation_context("cluster", 42, 1000, 1000)
    journal.record_reservation(
        "cluster",
        42,
        ReservationRecord("svc", 9, "runtime", 1, "accepted", "workload"),
    )
    journal.complete_allocation("cluster", 42, "accepted", {"unused": True})

    records = journal.reservation_context("cluster", 42, 1000, 1000)

    assert [(item.service_id, item.reservation_id) for item in records] == [
        ("svc", 9)
    ]
    with pytest.raises(RequestConflict):
        journal.reservation_context("cluster", 42, 1001, 1000)
    journal.close()
