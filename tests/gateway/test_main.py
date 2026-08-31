from __future__ import annotations

import socket

from qfw_slurm_gateway.__main__ import _notify_service_manager


def test_service_manager_notification(tmp_path, monkeypatch) -> None:
    path = tmp_path / "notify.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    listener.bind(str(path))
    listener.settimeout(1)
    monkeypatch.setenv("NOTIFY_SOCKET", str(path))
    try:
        _notify_service_manager("READY=1")
        assert listener.recv(64) == b"READY=1"
    finally:
        listener.close()
