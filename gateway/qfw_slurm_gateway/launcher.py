"""Prepare the QFw site directory environment before DEFw starts."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigurationError, GatewayConfig, load_config


DIRECTORY_SERVICE_SCHEMA = "qfw-directory-service-v1"
_ENVIRONMENT_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} must be a mapping")
    return value


def _expand_path(value: Any, base: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigurationError(
            "directory-service.connection-file must be a path"
        )

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        selected = os.environ.get(name)
        if not selected:
            raise ConfigurationError(
                "directory-service.connection-file references unset or "
                f"empty environment variable ${{{name}}}"
            )
        return selected

    expanded = _ENVIRONMENT_REFERENCE.sub(replace, value)
    if "$" in expanded:
        raise ConfigurationError(
            "directory-service.connection-file contains an unsupported "
            "environment reference"
        )
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _endpoint(value: Any) -> tuple[str, int, str]:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(
            "directory service endpoint must be a nonempty string"
        )
    endpoint = value.strip()
    if endpoint.startswith("["):
        end = endpoint.find("]")
        if end < 2 or end + 1 >= len(endpoint) or endpoint[end + 1] != ":":
            raise ConfigurationError(
                f"invalid directory service endpoint {endpoint!r}"
            )
        host = endpoint[1:end]
        port_text = endpoint[end + 2 :]
    else:
        try:
            host, port_text = endpoint.rsplit(":", 1)
        except ValueError as error:
            raise ConfigurationError(
                f"invalid directory service endpoint {endpoint!r}"
            ) from error
    try:
        port = int(port_text)
    except ValueError as error:
        raise ConfigurationError(
            f"invalid directory service endpoint {endpoint!r}"
        ) from error
    if not host or not 1 <= port <= 65535:
        raise ConfigurationError(
            f"invalid directory service endpoint {endpoint!r}"
        )
    return host, port, endpoint


def _read_site_config(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"cannot read QFw site config {path}: {error}") \
            from error
    return _mapping(raw, "QFw site configuration")


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("", 0))
        return int(listener.getsockname()[1])


def directory_environment(site_config: Path) -> dict[str, str]:
    """Return the DEFw environment for the configured site directory."""

    root = _read_site_config(site_config)
    directory = _mapping(
        root.get("directory-service"), "directory-service"
    )
    name = str(directory.get("name") or "qfw-site-dirsvc").strip()
    timeout = directory.get("connect-timeout-seconds", 300)
    if isinstance(timeout, bool):
        raise ConfigurationError(
            "directory-service.connect-timeout-seconds must be positive"
        )
    try:
        timeout = int(timeout)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            "directory-service.connect-timeout-seconds must be positive"
        ) from error
    if not name or timeout <= 0:
        raise ConfigurationError(
            "directory-service name and connect timeout must be valid"
        )

    connection_file = directory.get("connection-file")
    endpoint_value = directory.get("endpoint")
    connection_path = None
    if endpoint_value:
        endpoint_name = name
    elif connection_file:
        connection_path = _expand_path(connection_file, site_config.parent)
        try:
            record = json.loads(connection_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigurationError(
                "cannot read directory service connection record "
                f"{connection_path}: {error}"
            ) from error
        record = _mapping(record, "directory service connection record")
        if record.get("schema") != DIRECTORY_SERVICE_SCHEMA:
            raise ConfigurationError(
                "unsupported directory service connection record schema"
            )
        if record.get("ready") is not True:
            raise ConfigurationError("directory service is not ready")
        endpoint_value = record.get("endpoint")
        endpoint_name = str(record.get("name") or "").strip()
        if not endpoint_name:
            raise ConfigurationError(
                "directory service connection record lacks a name"
            )
    else:
        raise ConfigurationError(
            "directory-service requires connection-file or endpoint"
        )

    host, port, endpoint = _endpoint(endpoint_value)
    try:
        parent_address = socket.gethostbyname(host)
    except OSError:
        parent_address = host
    environment = {
        "QFW_SITE_CONFIG": str(site_config),
        "QFW_SITE_DIRSVC_ENDPOINTS": endpoint,
        "QFW_SITE_DIRSVC_NAME": endpoint_name,
        "QFW_DIRSVC_CONNECT_TIMEOUT_SECONDS": str(timeout),
        "DEFW_DISABLE_DIRSVC": "no",
        "DEFW_AGENT_TYPE": "agent",
        "DEFW_SHELL_TYPE": "cmdline",
        "DEFW_LISTEN_PORT": str(_free_tcp_port()),
        "DEFW_PARENT_ADDR": parent_address,
        "DEFW_PARENT_HOSTNAME": host,
        "DEFW_PARENT_PORT": str(port),
        "DEFW_PARENT_NAME": endpoint_name,
    }
    if connection_path is not None:
        environment["QFW_DIRECTORY_SERVICE_INFO"] = str(connection_path)
    return environment


def defwp_command(config: GatewayConfig, arguments: list[str]) -> list[str]:
    """Build the DEFw module command after validating activation."""

    if not config.qfw_activation.is_file():
        raise ConfigurationError(
            f"QFw activation script does not exist: {config.qfw_activation}"
        )
    qfw_prefix = os.environ.get("QFW_PREFIX")
    if not qfw_prefix:
        raise ConfigurationError(
            "QFW_PREFIX is unset; activate the configured QFw installation"
        )
    active_activation = Path(qfw_prefix) / "bin" / "qfw-activate"
    if active_activation.resolve() != config.qfw_activation.resolve():
        raise ConfigurationError(
            f"active QFw prefix {qfw_prefix} does not provide configured "
            f"activation {config.qfw_activation}"
        )
    if Path(sys.prefix).resolve() != config.qfw_venv.resolve():
        raise ConfigurationError(
            f"gateway is using {sys.prefix}, expected QFw venv "
            f"{config.qfw_venv}"
        )
    defw_prefix = os.environ.get("DEFW_PREFIX")
    if not defw_prefix:
        raise ConfigurationError(
            "DEFW_PREFIX is unset; activate the configured QFw installation"
        )
    defwp = Path(defw_prefix) / "bin" / "defwp"
    if not defwp.is_file() or not os.access(defwp, os.X_OK):
        raise ConfigurationError(f"DEFw launcher does not exist: {defwp}")
    return [
        str(defwp),
        "-m",
        "qfw_slurm_gateway",
        *arguments,
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qfw-slurm-gateway-launch")
    parser.add_argument(
        "--config", required=True, help="gateway YAML configuration"
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.arguments:
        raise SystemExit("a qfw-slurm-gateway command is required")
    try:
        config = load_config(args.config)
        environment = directory_environment(config.qfw_site_config)
        command = defwp_command(
            config, ["--config", args.config, *args.arguments]
        )
    except ConfigurationError as error:
        raise SystemExit(f"qfw-slurm-gateway-launch: {error}") from error
    os.environ.update(environment)
    os.execv(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
