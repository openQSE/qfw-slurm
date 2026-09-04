from __future__ import annotations

import json
import subprocess

import pytest

from qfw_slurm_inspect.slurm import SlurmCommandError, SlurmJsonClient


def _runner(payload, returncode=0, stderr=""):
    def run(command, **kwargs):
        assert command[-1] == "--json"
        assert kwargs["timeout"] == 2
        return subprocess.CompletedProcess(
            command, returncode, json.dumps(payload), stderr
        )

    return run


def test_normalizes_current_sinfo_schema() -> None:
    client = SlurmJsonClient(2, _runner({
        "errors": [],
        "sinfo": [{
            "node": {"state": ["IDLE"]},
            "nodes": {"nodes": ["nwqsim-head", "nwqsim-worker-1"]},
            "partition": {"name": "qfw-services"},
            "features": {"active": ["qfw-service", "nwqsim"]},
        }],
    }))

    nodes = client.nodes()

    assert [node.name for node in nodes] == [
        "nwqsim-head", "nwqsim-worker-1"
    ]
    assert nodes[0].state == "IDLE"
    assert nodes[0].features == ("qfw-service", "nwqsim")


def test_normalizes_heterogeneous_squeue_job() -> None:
    client = SlurmJsonClient(2, _runner({
        "errors": [],
        "meta": {"slurm": {"cluster": "qfw-cluster"}},
        "jobs": [{
            "job_id": 42,
            "user_name": "user-a",
            "job_state": ["RUNNING"],
            "nodes": "c1",
            "het_job_id": 40,
            "het_job_offset": {"number": 1},
        }],
    }))

    jobs = client.jobs()

    assert jobs[0].job_id == "42"
    assert jobs[0].heterogeneous_job_id == "40"
    assert jobs[0].heterogeneous_job_offset == 1
    assert jobs[0].nodes == ("c1",)
    assert jobs[0].cluster_name == "qfw-cluster"


def test_ignores_unset_heterogeneous_job_metadata() -> None:
    client = SlurmJsonClient(2, _runner({
        "errors": [],
        "jobs": [{
            "job_id": 42,
            "user_name": "user-a",
            "job_state": ["RUNNING"],
            "nodes": "c1",
            "het_job_id": {"set": True, "infinite": False, "number": 0},
            "het_job_offset": {
                "set": True, "infinite": False, "number": 0
            },
        }],
    }))

    jobs = client.jobs()

    assert jobs[0].job_id == "42"
    assert jobs[0].heterogeneous_job_id == ""


def test_rejects_command_errors() -> None:
    client = SlurmJsonClient(2, _runner({}, returncode=1, stderr="down"))

    with pytest.raises(SlurmCommandError, match="down"):
        client.jobs()
