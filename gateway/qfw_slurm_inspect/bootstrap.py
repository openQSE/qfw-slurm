"""Launch inspection commands as DEFw client processes."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


class InspectionBootstrapError(RuntimeError):
    """Raised when an inspection command cannot join DEFw."""


def sinfo_main() -> int:
    """Launch qfw-sinfo beneath the activated DEFw runtime."""

    return _launch("qfw-sinfo", "qfw_slurm_inspect.sinfo")


def finish_defw_command(status: int) -> None:
    """Shut down the DEFw client runtime and preserve command status."""

    import defw

    try:
        defw.me.exit()
    except SystemExit as error:
        raise SystemExit(status) from error


def _launch(program: str, module: str) -> int:
    if any(argument in {"-h", "--help"} for argument in sys.argv[1:]):
        from .sinfo import main

        return main(sys.argv[1:])
    try:
        site_path, directory = _directory_configuration(sys.argv[1:])
        environment = _directory_environment(site_path, directory)
        defw_prefix = os.environ.get("DEFW_PREFIX")
        if not defw_prefix:
            raise InspectionBootstrapError(
                "DEFW_PREFIX is unset; activate QFw before running this command"
            )
        defwp = Path(defw_prefix) / "bin" / "defwp"
        if not defwp.is_file() or not os.access(defwp, os.X_OK):
            raise InspectionBootstrapError(
                f"DEFw launcher is not executable: {defwp}"
            )
    except (InspectionBootstrapError, OSError, ValueError) as error:
        raise SystemExit(f"{program}: {error}") from error
    os.environ.update(environment)
    command = [str(defwp), "-m", module, *sys.argv[1:]]
    os.execv(command[0], command)
    return 127


def _directory_configuration(arguments: list[str]):
    try:
        from qfw_runtime.config import (
            load_yaml,
            resolve_site_config,
            site_directory,
        )
    except ImportError as error:
        raise InspectionBootstrapError(
            "QFw runtime is unavailable; activate QFw before running this command"
        ) from error
    explicit = None
    for index, argument in enumerate(arguments):
        if argument == "--site-config" and index + 1 < len(arguments):
            explicit = arguments[index + 1]
        elif argument.startswith("--site-config="):
            explicit = argument.split("=", 1)[1]
    site_path = resolve_site_config(explicit)
    site = load_yaml(site_path)
    directory = site_directory(site, site_config_path=site_path)
    if not directory.get("endpoint"):
        raise InspectionBootstrapError(
            "QFw site configuration did not resolve a directory endpoint"
        )
    return site_path, directory


def _directory_environment(site_path: Path, directory: dict) -> dict[str, str]:
    host, port = _split_endpoint(str(directory["endpoint"]))
    try:
        address = socket.gethostbyname(host)
    except OSError:
        address = host
    name = str(directory.get("name") or "qfw-site-dirsvc")
    environment = {
        "QFW_SITE_CONFIG": str(site_path),
        "QFW_SITE_DIRSVC_ENDPOINTS": str(directory["endpoint"]),
        "QFW_SITE_DIRSVC_NAME": name,
        "QFW_DIRSVC_CONNECT_TIMEOUT_SECONDS": str(
            directory.get("connect_timeout_seconds", 300)
        ),
        "DEFW_DISABLE_DIRSVC": "no",
        "DEFW_AGENT_TYPE": "agent",
        "DEFW_SHELL_TYPE": "cmdline",
        "DEFW_LISTEN_PORT": str(_free_port()),
        "DEFW_PARENT_ADDR": address,
        "DEFW_PARENT_HOSTNAME": host,
        "DEFW_PARENT_PORT": str(port),
        "DEFW_PARENT_NAME": name,
    }
    connection_file = directory.get("connection_file")
    if connection_file:
        environment["QFW_DIRECTORY_SERVICE_INFO"] = str(connection_file)
    return environment


def _split_endpoint(endpoint: str) -> tuple[str, int]:
    if endpoint.startswith("["):
        close = endpoint.find("]")
        if close < 2 or endpoint[close + 1 : close + 2] != ":":
            raise InspectionBootstrapError(
                f"invalid directory endpoint {endpoint!r}"
            )
        host = endpoint[1:close]
        port_text = endpoint[close + 2 :]
    else:
        try:
            host, port_text = endpoint.rsplit(":", 1)
        except ValueError as error:
            raise InspectionBootstrapError(
                f"invalid directory endpoint {endpoint!r}"
            ) from error
    try:
        port = int(port_text)
    except ValueError as error:
        raise InspectionBootstrapError(
            f"invalid directory endpoint {endpoint!r}"
        ) from error
    if not host or not 1 <= port <= 65535:
        raise InspectionBootstrapError(
            f"invalid directory endpoint {endpoint!r}"
        )
    return host, port


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("", 0))
        return int(listener.getsockname()[1])
