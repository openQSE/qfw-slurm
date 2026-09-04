from __future__ import annotations

import sys
import types

import pytest

from qfw_slurm_inspect import bootstrap


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
