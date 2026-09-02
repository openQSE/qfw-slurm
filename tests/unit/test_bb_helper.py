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


def run(command, log: Path, mode="accepted"):
    environment = os.environ.copy()
    environment["QFW_TEST_DRIVER_LOG"] = str(log)
    environment["QFW_TEST_DRIVER_MODE"] = mode
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


def test_reserve_paths_and_best_effort_release(tmp_path: Path) -> None:
    source = Path(os.environ["QFW_TEST_BB_HELPER"])
    fake = Path(os.environ["QFW_TEST_FAKE_DRIVER"])
    log = tmp_path / "calls"
    reserve = command(source, fake, tmp_path, "reserve")

    assert run(reserve, log).returncode == 0
    paths = command(source, fake, tmp_path, "paths")
    path_file = tmp_path / "environment"
    paths.extend(["--path-file", str(path_file)])
    assert run(paths, log).returncode == 0
    assert path_file.read_text() == (
        'QFW_RESERVATIONS=[["nwqsim-site","41"]]\n'
    )
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
