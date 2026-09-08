from __future__ import annotations

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
