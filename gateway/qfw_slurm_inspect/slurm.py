"""Bounded JSON access to installed Slurm commands."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

from .models import SlurmJob, SlurmNode


class SlurmCommandError(RuntimeError):
    """Raised when Slurm cannot provide a valid JSON response."""


class SlurmJsonClient:
    """Run ``sinfo`` and ``squeue`` without coupling rendering to Slurm I/O."""

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ):
        if timeout_seconds <= 0:
            raise ValueError("Slurm command timeout must be positive")
        self.timeout_seconds = timeout_seconds
        self._runner = runner or subprocess.run

    def nodes(self) -> list[SlurmNode]:
        payload = self._run("sinfo", "--Node")
        rows = payload.get("sinfo")
        if not isinstance(rows, list):
            raise SlurmCommandError("sinfo JSON response lacks a sinfo list")
        result = []
        for row in rows:
            if not isinstance(row, dict):
                raise SlurmCommandError("sinfo JSON contains a malformed row")
            node_names = _nested_list(row, "nodes", "nodes")
            if not node_names:
                continue
            state = _first_text(_nested(row, "node", "state"), "UNKNOWN")
            partition = str(_nested(row, "partition", "name") or "")
            features = tuple(
                _texts(_nested(row, "features", "active"))
                or _texts(_nested(row, "features", "total"))
            )
            for name in node_names:
                result.append(SlurmNode(
                    name=str(name),
                    state=state,
                    partition=partition,
                    features=features,
                ))
        return result

    def jobs(self) -> list[SlurmJob]:
        payload = self._run("squeue")
        rows = payload.get("jobs")
        if not isinstance(rows, list):
            raise SlurmCommandError("squeue JSON response lacks a jobs list")
        cluster_name = str(
            _nested(payload, "meta", "slurm", "cluster") or ""
        )
        return [_job(row, cluster_name) for row in rows]

    def _run(self, command: str, *arguments: str) -> dict[str, Any]:
        invocation = [command, *arguments, "--json"]
        try:
            completed = self._runner(
                invocation,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SlurmCommandError(
                f"{command} did not complete: {error}"
            ) from error
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise SlurmCommandError(
                f"{command} exited with {completed.returncode}: "
                f"{detail or 'no diagnostic'}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise SlurmCommandError(
                f"{command} returned malformed JSON: {error}"
            ) from error
        if not isinstance(payload, dict):
            raise SlurmCommandError(
                f"{command} JSON response must be an object"
            )
        errors = payload.get("errors") or []
        if errors:
            raise SlurmCommandError(f"{command} reported errors: {errors}")
        return payload


def _job(row: Any, default_cluster: str = "") -> SlurmJob:
    if not isinstance(row, dict):
        raise SlurmCommandError("squeue JSON contains a malformed job")
    job_id = _identifier(row.get("job_id"))
    if not job_id:
        raise SlurmCommandError("squeue job lacks job_id")
    user = str(row.get("user_name") or row.get("user") or "")
    state = _first_text(row.get("job_state"), "UNKNOWN")
    nodes = tuple(_texts(row.get("nodes")))
    heterogeneous_id = _optional_identifier(
        row.get("het_job_id") or row.get("heterogeneous_job_id")
    )
    offset = row.get("het_job_offset", row.get("heterogeneous_job_offset"))
    if isinstance(offset, dict):
        offset = offset.get("number")
    try:
        offset = int(offset) if offset is not None else None
    except (TypeError, ValueError):
        offset = None
    return SlurmJob(
        job_id=job_id,
        user=user,
        state=state,
        nodes=nodes,
        cluster_name=str(row.get("cluster") or default_cluster),
        heterogeneous_job_id=heterogeneous_id,
        heterogeneous_job_offset=offset,
    )


def _identifier(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("number")
    if value is None:
        return ""
    return str(value)


def _optional_identifier(value: Any) -> str:
    identifier = _identifier(value)
    return "" if identifier in {"", "0"} else identifier


def _nested(mapping: dict[str, Any], *path: str) -> Any:
    value: Any = mapping
    for name in path:
        if not isinstance(value, dict):
            return None
        value = value.get(name)
    return value


def _nested_list(mapping: dict[str, Any], *path: str) -> list[Any]:
    value = _nested(mapping, *path)
    return value if isinstance(value, list) else []


def _texts(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item for item in value.split(",") if item]
    if isinstance(value, Sequence):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _first_text(value: Any, default: str) -> str:
    values = _texts(value)
    return values[0] if values else default
