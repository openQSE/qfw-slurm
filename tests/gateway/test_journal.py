from __future__ import annotations

import pytest

from qfw_slurm_gateway.journal import Journal, RequestConflict, ReservationRecord


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
