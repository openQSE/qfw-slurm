from __future__ import annotations

import os
import sys
import types

import pytest

from qfw_slurm_inspect import bootstrap


def test_directory_environment_loads_only_directory_client_api(
    tmp_path,
) -> None:
    environment = bootstrap._directory_environment(
        tmp_path / "site.yaml",
        {
            "endpoint": "127.0.0.1:18090",
            "name": "directory",
        },
    )

    assert environment["DEFW_ONLY_LOAD_MODULE"] == "api_dirsvc"


def test_finish_defw_command_preserves_status(monkeypatch) -> None:
    calls = []

    def exit_defw() -> None:
        calls.append("exit")
        raise SystemExit(0)

    fake = types.SimpleNamespace(
        me=types.SimpleNamespace(exit=exit_defw)
    )
    monkeypatch.setitem(sys.modules, "defw", fake)

    with pytest.raises(SystemExit) as error:
        bootstrap.finish_defw_command(7)

    assert error.value.code == 7
    assert calls == ["exit"]


def _prepared_launch(tmp_path, monkeypatch, launchers):
    bin_dir = tmp_path / "defw" / "bin"
    bin_dir.mkdir(parents=True)
    for name in launchers:
        (bin_dir / name).touch()
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("DEFW_PREFIX", str(bin_dir.parent))
    # The launch exports the directory environment before it replaces the
    # process, so the test gives it a copy to write into.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(sys, "argv", ["qfw-sinfo", "nwqsim-head"])
    monkeypatch.setattr(
        bootstrap,
        "_directory_configuration",
        lambda arguments: (
            tmp_path / "site.yaml",
            {"endpoint": "127.0.0.1:18090", "name": "directory"},
        ),
    )
    executed = []
    monkeypatch.setattr(
        os, "execv", lambda path, command: executed.append((path, command))
    )
    return bin_dir, executed


def test_launch_runs_the_command_through_defw2_python(
    tmp_path, monkeypatch
) -> None:
    bin_dir, executed = _prepared_launch(
        tmp_path, monkeypatch, ("defw2-python",)
    )

    assert bootstrap.sinfo_main() == 127

    program = str(bin_dir / "defw2-python")
    assert executed == [
        (program, [program, "-m", "qfw_slurm_inspect.sinfo", "nwqsim-head"])
    ]


def test_launch_needs_the_defw_v2_launcher(tmp_path, monkeypatch) -> None:
    # An installation with only DEFw v1's launcher cannot run the command.
    bin_dir, executed = _prepared_launch(tmp_path, monkeypatch, ("defwp",))

    with pytest.raises(SystemExit) as error:
        bootstrap.sinfo_main()

    assert str(bin_dir / "defw2-python") in str(error.value)
    assert executed == []
