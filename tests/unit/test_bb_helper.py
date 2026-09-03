from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def command(source: Path, fake: Path, work: Path, operation: str):
    directive = work / "directive"
    directive.write_text(
        "#QFW v=1 qpu=nwqsim-site workload=quantum circuits=2 "
        "qubits=5 depth=100 shots=1024\n",
        encoding="utf-8",
    )
    output = [
        sys.executable,
        str(source),
        operation,
        "--driver",
        str(fake),
        "--plugin-config",
        str(work / "plugin.conf"),
        "--state-dir",
        str(work / "state"),
        "--cluster",
        "test-cluster",
        "--job-id",
        "41",
        "--canonical-job-id",
        "41",
        "--uid",
        "1001",
        "--gid",
        "1001",
        "--submit-time",
        "1788000000",
        "--walltime-seconds",
        "60",
        "--job-script",
        str(directive),
    ]
    return output


def run(command, log: Path, mode="accepted", expected_job_id=None):
    environment = os.environ.copy()
    environment["QFW_TEST_DRIVER_LOG"] = str(log)
    environment["QFW_TEST_DRIVER_MODE"] = mode
    if expected_job_id is not None:
        environment["QFW_TEST_EXPECT_JOB_ID"] = str(expected_job_id)
    return subprocess.run(command, env=environment, check=False, text=True,
                          capture_output=True)


def test_classical_wait_holds_no_quantum_reservation(tmp_path: Path) -> None:
    source = Path(os.environ["QFW_TEST_BB_HELPER"])
    fake = Path(os.environ["QFW_TEST_FAKE_DRIVER"])
    log = tmp_path / "calls"
    evaluate = command(source, fake, tmp_path, "evaluate")

    assert run(evaluate, log).returncode == 0
    state = json.loads(next((tmp_path / "state").iterdir()).read_text())
    assert state["state"] == "evaluation-accepted"
    assert "reservations" not in state
    assert log.read_text().splitlines() == ["evaluate"]


def test_delayed_evaluation_is_retryable(tmp_path: Path) -> None:
    source = Path(os.environ["QFW_TEST_BB_HELPER"])
    fake = Path(os.environ["QFW_TEST_FAKE_DRIVER"])
    log = tmp_path / "calls"
    evaluate = command(source, fake, tmp_path, "evaluate")

    assert run(evaluate, log, "delayed").returncode == 10
    assert run(evaluate, log, "accepted").returncode == 0
    assert log.read_text().splitlines() == ["evaluate", "evaluate"]


def test_reserve_records_controller_state_and_releases(tmp_path: Path) -> None:
    source = Path(os.environ["QFW_TEST_BB_HELPER"])
    fake = Path(os.environ["QFW_TEST_FAKE_DRIVER"])
    log = tmp_path / "calls"
    reserve = command(source, fake, tmp_path, "reserve")

    assert run(reserve, log).returncode == 0
    assert list((tmp_path / "state").glob("*.env")) == []
    state = json.loads(next((tmp_path / "state").glob("*.json")).read_text())
    assert state["reservations"] == [["nwqsim-site", "41"]]
    release = command(source, fake, tmp_path, "release")
    assert run(release, log).returncode == 0
    state = json.loads(next((tmp_path / "state").iterdir()).read_text())
    assert state["state"] == "released"


def test_final_delay_records_no_reservation(tmp_path: Path) -> None:
    source = Path(os.environ["QFW_TEST_BB_HELPER"])
    fake = Path(os.environ["QFW_TEST_FAKE_DRIVER"])
    log = tmp_path / "calls"
    reserve = command(source, fake, tmp_path, "reserve")

    assert run(reserve, log, "delayed").returncode == 10
    state = json.loads(next((tmp_path / "state").iterdir()).read_text())
    assert state["state"] == "reservation-delayed"
    assert "reservations" not in state


def test_release_recovers_when_local_state_is_missing(tmp_path: Path) -> None:
    source = Path(os.environ["QFW_TEST_BB_HELPER"])
    fake = Path(os.environ["QFW_TEST_FAKE_DRIVER"])
    log = tmp_path / "calls"
    release = command(source, fake, tmp_path, "release")

    assert run(release, log).returncode == 0
    assert log.read_text().splitlines() == ["release"]
    state = json.loads(next((tmp_path / "state").glob("*.json")).read_text())
    assert state["state"] == "released"
    assert state["diagnostic"] == "local reservation state was unavailable"


def test_final_reservation_attempt_limit_is_terminal(tmp_path: Path) -> None:
    source = Path(os.environ["QFW_TEST_BB_HELPER"])
    fake = Path(os.environ["QFW_TEST_FAKE_DRIVER"])
    log = tmp_path / "calls"
    reserve = command(source, fake, tmp_path, "reserve")
    reserve.extend(["--max-reservation-attempts", "2"])

    assert run(reserve, log, "delayed").returncode == 10
    assert run(reserve, log, "delayed").returncode == 10
    assert run(reserve, log, "accepted").returncode == 20
    assert log.read_text().splitlines() == ["reserve", "reserve"]
    state = json.loads(next((tmp_path / "state").glob("*.json")).read_text())
    assert state["state"] == "reservation-exhausted"
    assert state["reservation_attempts"] == 2


def test_heterogeneous_release_uses_canonical_job_identity(tmp_path: Path) -> None:
    source = Path(os.environ["QFW_TEST_BB_HELPER"])
    fake = Path(os.environ["QFW_TEST_FAKE_DRIVER"])
    log = tmp_path / "calls"
    reserve = command(source, fake, tmp_path, "reserve")
    reserve[reserve.index("--job-id") + 1] = "42"
    reserve.extend(["--het-job-id", "42", "--het-component", "1"])

    assert run(reserve, log).returncode == 0
    release = command(source, fake, tmp_path, "release")
    release[release.index("--job-id") + 1] = "42"
    release[release.index("--canonical-job-id") + 1] = "42"
    assert run(release, log, expected_job_id=41).returncode == 0
    canonical = json.loads(
        (tmp_path / "state" / "test-cluster-41.json").read_text()
    )
    component = json.loads(
        (tmp_path / "state" / "test-cluster-42.json").read_text()
    )
    assert canonical["state"] == "released"
    assert component["state"] == "released"
