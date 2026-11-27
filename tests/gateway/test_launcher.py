from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from qfw_slurm_gateway.config import ConfigurationError, GatewayConfig
from qfw_slurm_gateway.launcher import defw_command, directory_environment


def _write_site(tmp_path: Path, connection: str) -> Path:
    site = tmp_path / "site.yaml"
    site.write_text(
        "directory-service:\n"
        "  name: configured-name\n"
        "  connect-timeout-seconds: 45\n"
        f"  connection-file: {connection}\n",
        encoding="utf-8",
    )
    return site


def test_connection_record_prepares_defw_parent(tmp_path, monkeypatch) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    record = shared / "directory-service.json"
    record.write_text(
        json.dumps(
            {
                "schema": "qfw-directory-service-v1",
                "ready": True,
                "name": "running-directory",
                "endpoint": "localhost:18090",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QFW_SHARED_ROOT", str(shared))
    site = _write_site(
        tmp_path,
        "${QFW_SHARED_ROOT}/directory-service.json",
    )

    environment = directory_environment(site)

    assert environment["QFW_SITE_DIRSVC_ENDPOINTS"] == "localhost:18090"
    assert environment["QFW_SITE_DIRSVC_NAME"] == "running-directory"
    assert environment["QFW_DIRECTORY_SERVICE_INFO"] == str(record)
    assert environment["DEFW_PARENT_HOSTNAME"] == "localhost"
    assert environment["DEFW_PARENT_PORT"] == "18090"
    assert environment["DEFW_PARENT_NAME"] == "running-directory"
    assert environment["DEFW_DISABLE_DIRSVC"] == "no"
    assert environment["DEFW_ONLY_LOAD_MODULE"] == "api_dirsvc"
    assert 0 < int(environment["DEFW_LISTEN_PORT"]) <= 65535


def test_stable_endpoint_does_not_require_connection_record(tmp_path) -> None:
    site = tmp_path / "site.yaml"
    site.write_text(
        "directory-service:\n"
        "  name: stable-directory\n"
        "  endpoint: '[2001:db8::4]:8090'\n",
        encoding="utf-8",
    )

    environment = directory_environment(site)

    assert environment["DEFW_PARENT_HOSTNAME"] == "2001:db8::4"
    assert environment["DEFW_PARENT_PORT"] == "8090"
    assert "QFW_DIRECTORY_SERVICE_INFO" not in environment


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema", "wrong", "unsupported"),
        ("ready", False, "not ready"),
        ("endpoint", "missing-port", "invalid"),
    ],
)
def test_invalid_connection_record_is_rejected(
    tmp_path, field, value, message
) -> None:
    record_data = {
        "schema": "qfw-directory-service-v1",
        "ready": True,
        "name": "directory",
        "endpoint": "localhost:18090",
    }
    record_data[field] = value
    record = tmp_path / "directory-service.json"
    record.write_text(json.dumps(record_data), encoding="utf-8")
    site = _write_site(tmp_path, str(record))

    with pytest.raises(ConfigurationError, match=message):
        directory_environment(site)


def _activated_installation(tmp_path, monkeypatch, launchers):
    activation = tmp_path / "qfw" / "bin" / "qfw-activate"
    activation.parent.mkdir(parents=True)
    activation.touch()
    venv = tmp_path / "venv"
    venv.mkdir()
    bin_dir = tmp_path / "defw" / "bin"
    bin_dir.mkdir(parents=True)
    for name in launchers:
        (bin_dir / name).touch()
        (bin_dir / name).chmod(0o755)
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setenv("QFW_PREFIX", str(activation.parent.parent))
    monkeypatch.setenv("DEFW_PREFIX", str(bin_dir.parent))
    config = GatewayConfig(
        listen_host="127.0.0.1",
        listen_port=18095,
        accepted_uids=frozenset({0}),
        cluster_name="cluster",
        journal_path=tmp_path / "journal",
        qfw_activation=activation,
        qfw_venv=venv,
        qfw_site_config=tmp_path / "site.yaml",
    )
    return config, bin_dir


def test_defw_command_uses_activated_installation(tmp_path, monkeypatch) -> None:
    config, bin_dir = _activated_installation(
        tmp_path, monkeypatch, ("defw2-python",)
    )

    command = defw_command(
        config, ["--config", "/etc/openqse/qfw-slurm/gateway.yaml", "serve"]
    )

    assert command == [
        str(bin_dir / "defw2-python"),
        "-m",
        "qfw_slurm_gateway",
        "--config",
        "/etc/openqse/qfw-slurm/gateway.yaml",
        "serve",
    ]


def test_defw_command_needs_the_defw_v2_launcher(tmp_path, monkeypatch) -> None:
    # An installation with only DEFw v1's launcher cannot run the gateway.
    config, bin_dir = _activated_installation(
        tmp_path, monkeypatch, ("defwp",)
    )

    with pytest.raises(ConfigurationError) as error:
        defw_command(config, ["serve"])

    assert str(bin_dir / "defw2-python") in str(error.value)
