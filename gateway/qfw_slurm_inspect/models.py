"""Typed internal records shared by qfw-slurm inspection commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SlurmNode:
    """A node record normalized from ``sinfo --json``."""

    name: str
    state: str
    partition: str = ""
    features: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlurmJob:
    """A job or heterogeneous component normalized from ``squeue --json``."""

    job_id: str
    user: str
    state: str
    nodes: tuple[str, ...] = ()
    cluster_name: str = ""
    heterogeneous_job_id: str = ""
    heterogeneous_job_offset: int | None = None


@dataclass(frozen=True)
class QPMService:
    """Sanitized QPM identity and service-summary data."""

    service_id: str
    runtime_id: str
    generation: int
    backend: str
    state: str
    ready: bool
    active_reservations: int
    active_tasks: int
    assigned_hosts: tuple[str, ...] = ()
    dvm_ready: bool | None = None
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class QPMAllocation:
    """Sanitized scheduler allocation returned by QPM telemetry."""

    service_id: str
    scheduler: str
    cluster_name: str
    allocation_id: str
    job_id: str
    user: str
    state: str
    active_tasks: int
    workload_kind: str = ""
    created_ns: int | None = None
    updated_ns: int | None = None
