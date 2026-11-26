"""DEFw process-role configuration shared by qfw-slurm clients."""

from __future__ import annotations

import os
from collections.abc import Mapping

DEFW_VERSION_VARIABLE = "QFW_DEFW_VERSION"
_LAUNCHERS = {1: "defwp", 2: "defw2-python"}


def client_environment() -> dict[str, str]:
    """Return the DEFw module boundary for a directory-service client."""

    return {"DEFW_ONLY_LOAD_MODULE": "api_dirsvc"}


def defw_version(environment: Mapping[str, str] | None = None) -> int:
    """Return the DEFw a client process runs on: 1, or 2 for DEFw v2.

    QFW_DEFW_VERSION selects it, as it does for QFw's own processes. Unset
    or empty means 1.
    """

    source = os.environ if environment is None else environment
    selected = source.get(DEFW_VERSION_VARIABLE, "").strip()
    if selected in ("", "1"):
        return 1
    if selected == "2":
        return 2
    raise ValueError(
        f"{DEFW_VERSION_VARIABLE} must be 1 or 2, not {selected!r}"
    )


def launcher_name(environment: Mapping[str, str] | None = None) -> str:
    """Return the DEFw program that runs a Python module as a client.

    DEFw v1's defwp embeds the interpreter. DEFw v2's defw2-python runs the
    active interpreter with v1's client modules served by v2. Both take
    ``-m MODULE`` and find the directory from the same parent variables.
    """

    return _LAUNCHERS[defw_version(environment)]
