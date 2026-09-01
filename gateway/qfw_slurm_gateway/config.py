"""Configuration loading and validation for qfw-slurm-gateway."""

from __future__ import annotations

import dataclasses
import os
import pwd
import stat
from pathlib import Path
from typing import Any

import yaml


class ConfigurationError(ValueError):
    """Raised when gateway configuration is unsafe or incomplete."""


@dataclasses.dataclass(frozen=True)
class GatewayConfig:
    listen_host: str
    listen_port: int
    accepted_uids: frozenset[int]
    cluster_name: str
    journal_path: Path
    qfw_activation: Path
    qfw_venv: Path
    qfw_site_config: Path
    request_timeout_seconds: float = 30.0
    connect_timeout_seconds: float = 5.0
    max_credential_size: int = 512 * 1024
    max_frame_size: int = 256 * 1024
    slurm_command: str = "scontrol"


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} must be a mapping")
    return value


def _required(mapping: dict[str, Any], name: str, label: str) -> Any:
    value = mapping.get(name)
    if value in (None, ""):
        raise ConfigurationError(f"{label}.{name} is required")
    return value


def _uid(value: Any) -> int:
    if isinstance(value, bool):
        raise ConfigurationError("accepted UID cannot be boolean")
    if isinstance(value, int):
        uid = value
    elif isinstance(value, str) and value.isdecimal():
        uid = int(value)
    elif isinstance(value, str):
        try:
            uid = pwd.getpwnam(value).pw_uid
        except KeyError as error:
            raise ConfigurationError(f"unknown accepted user {value!r}") from error
    else:
        raise ConfigurationError("accepted UID must be a user name or integer")
    if not 0 <= uid < 1 << 32:
        raise ConfigurationError("accepted UID is outside uint32")
    return uid


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{label} must be numeric")
    if value <= 0:
        raise ConfigurationError(f"{label} must be positive")
    return float(value)


def _bounded_size(value: Any, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{label} must be an integer")
    if not 1 <= value <= maximum:
        raise ConfigurationError(f"{label} is outside its protocol bound")
    return value


def _port(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError("gateway.listen-port must be an integer")
    if not 1 <= value <= 65535:
        raise ConfigurationError("gateway.listen-port is outside 1..65535")
    return value


def _path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{label} must be a path")
    return Path(os.path.expandvars(value)).expanduser()


def _validate_protected_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ConfigurationError(f"cannot inspect {path}: {error}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise ConfigurationError(
            "gateway configuration must be a protected root-owned file"
        )


def load_config(
    path: str | os.PathLike[str], *, validate_permissions: bool = True
) -> GatewayConfig:
    """Load a strict gateway YAML file."""

    config_path = Path(path)
    if validate_permissions:
        _validate_protected_file(config_path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"cannot read {config_path}: {error}") from error
    root = _mapping(raw, "configuration")
    listen = _mapping(_required(root, "listen", "configuration"), "listen")
    auth = _mapping(
        _required(root, "authentication", "configuration"), "authentication"
    )
    slurm = _mapping(_required(root, "slurm", "configuration"), "slurm")
    qfw = _mapping(_required(root, "qfw", "configuration"), "qfw")
    journal = _mapping(_required(root, "journal", "configuration"), "journal")

    mechanism = auth.get("mechanism")
    if mechanism != "munge":
        raise ConfigurationError("authentication.mechanism must be 'munge'")
    if auth.get("expected-plugin-name") != "spank_quantum":
        raise ConfigurationError(
            "authentication.expected-plugin-name must be 'spank_quantum'"
        )
    accepted = auth.get("accepted-uids")
    if not isinstance(accepted, list) or not accepted:
        raise ConfigurationError("authentication.accepted-uids must be nonempty")

    cluster_name = _required(slurm, "cluster-name", "slurm")
    if not isinstance(cluster_name, str):
        raise ConfigurationError("slurm.cluster-name must be a string")
    if slurm.get("verifier", "scontrol-json") != "scontrol-json":
        raise ConfigurationError("slurm.verifier must be 'scontrol-json'")
    if "adapter" in qfw:
        raise ConfigurationError(
            "qfw.adapter is not a production gateway configuration field"
        )
    listen_host = listen.get("host", "127.0.0.1")
    if not isinstance(listen_host, str) or not listen_host:
        raise ConfigurationError("listen.host must be a nonempty string")

    return GatewayConfig(
        listen_host=listen_host,
        listen_port=_port(_required(listen, "port", "listen")),
        accepted_uids=frozenset(_uid(value) for value in accepted),
        cluster_name=cluster_name,
        journal_path=_path(_required(journal, "path", "journal"), "journal.path"),
        qfw_activation=_path(
            _required(qfw, "activation", "qfw"), "qfw.activation"
        ),
        qfw_venv=_path(_required(qfw, "venv", "qfw"), "qfw.venv"),
        qfw_site_config=_path(
            _required(qfw, "site-config", "qfw"), "qfw.site-config"
        ),
        request_timeout_seconds=_positive_number(
            listen.get("request-timeout-seconds", 30),
            "listen.request-timeout-seconds",
        ),
        connect_timeout_seconds=_positive_number(
            listen.get("connect-timeout-seconds", 5),
            "listen.connect-timeout-seconds",
        ),
        max_credential_size=_bounded_size(
            listen.get("max-credential-bytes", 512 * 1024),
            "listen.max-credential-bytes",
            512 * 1024,
        ),
        max_frame_size=_bounded_size(
            listen.get("max-frame-bytes", 256 * 1024),
            "listen.max-frame-bytes",
            256 * 1024,
        ),
        slurm_command=str(slurm.get("command", "scontrol")),
    )
