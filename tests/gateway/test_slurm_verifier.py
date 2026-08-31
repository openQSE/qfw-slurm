from __future__ import annotations

import dataclasses
import json
import subprocess

import pytest

from qfw_slurm_gateway.protocol import (
    ReleaseRequest,
    ReserveRequest,
    Workload,
    WorkloadKind,
)
from qfw_slurm_gateway.slurm_verifier import (
    SlurmVerificationError,
    SlurmVerifier,
)


def request() -> ReserveRequest:
    return ReserveRequest(
        1,
        "qfw-cluster",
        42,
        1001,
        1002,
        Workload(WorkloadKind.QUANTUM, 10, 1, 2, 3, 4),
        ("nwqsim",),
    )


def runner(record):
    def run(*_args, **_kwargs):
        records = record if isinstance(record, list) else [record]
        return subprocess.CompletedProcess(
            [], 0, json.dumps({"jobs": records}), ""
        )

    return run


def test_verified_identity_comes_from_slurm() -> None:
    record = {
        "job_id": 42,
        "user_id": 1001,
        "group_id": {"number": 1002},
        "job_state": ["RUNNING"],
        "account": "project",
        "qos": "normal",
        "priority": {"number": 10},
    }
    verifier = SlurmVerifier("qfw-cluster", runner=runner(record))
    job = verifier.verify_reserve(request(), 1001)
    assert job.uid == 1001
    assert job.account == "project"


def test_claimed_uid_must_match_slurm() -> None:
    record = {
        "job_id": 42,
        "user_id": 2000,
        "group_id": 1002,
        "job_state": "RUNNING",
    }
    verifier = SlurmVerifier("qfw-cluster", runner=runner(record))
    with pytest.raises(SlurmVerificationError, match="differs"):
        verifier.verify_reserve(request(), 1001)


def test_verifies_heterogeneous_component() -> None:
    leader = {
        "job_id": 42,
        "user_id": 1001,
        "group_id": 1002,
        "job_state": "RUNNING",
        "het_job_id": {"number": 42},
        "het_job_offset": {"number": 0},
    }
    component = {
        **leader,
        "job_id": 43,
        "het_job_offset": {"number": 1},
    }
    verifier = SlurmVerifier(
        "qfw-cluster", runner=runner([leader, component])
    )
    selected = dataclasses.replace(
        request(), hetero_job_id=43, hetero_component=1
    )

    assert verifier.verify_reserve(selected, 1001).canonical_job_id == 42

    invalid = dataclasses.replace(selected, hetero_component=2)
    with pytest.raises(SlurmVerificationError, match="differs"):
        verifier.verify_reserve(invalid, 1001)


def test_protected_daemon_identity_can_manage_another_users_job() -> None:
    record = {
        "job_id": 42,
        "user_id": 1001,
        "group_id": 1002,
        "job_state": "COMPLETED",
    }
    verifier = SlurmVerifier(
        "qfw-cluster",
        trusted_sender_uids=frozenset({990}),
        runner=runner(record),
    )
    release = ReleaseRequest(7, "qfw-cluster", 42, 0)

    assert verifier.verify_release(release, 990, 1001).uid == 1001
    with pytest.raises(SlurmVerificationError, match="cannot release"):
        verifier.verify_release(release, 991, 1001)
