"""DEFw process-role configuration shared by qfw-slurm clients."""

from __future__ import annotations


def client_environment() -> dict[str, str]:
    """Return the DEFw module boundary for a directory-service client."""

    return {"DEFW_ONLY_LOAD_MODULE": "api_dirsvc"}
