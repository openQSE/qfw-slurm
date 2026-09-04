"""Combined Slurm job and sanitized QPM allocation status."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from typing import Any

from .models import QPMAllocation, SlurmJob
from .qpm import QPMInspectionClient, QPMInspectionError
from .render import table
from .slurm import SlurmCommandError, SlurmJsonClient


SCHEMA = "qfw-squeue-v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qfw-squeue",
        description="show Slurm jobs with sanitized QPM allocation state",
    )
    parser.add_argument("--job", help="show one canonical Slurm job ID")
    parser.add_argument("--user", help="show jobs owned by one user")
    parser.add_argument(
        "--json", action="store_true", help="write the versioned JSON response"
    )
    parser.add_argument(
        "--site-config", help="QFw site configuration used for discovery"
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="data-source timeout in seconds"
    )
    return parser


def main(arguments: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(arguments)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    errors = []
    try:
        jobs = SlurmJsonClient(args.timeout).jobs()
    except SlurmCommandError as error:
        jobs = []
        errors.append(str(error))
    try:
        qpm_client = QPMInspectionClient.connect(args.timeout)
        allocations = qpm_client.allocations(
            {"scheduler": "slurm"}
        )
        errors.extend(qpm_client.errors)
    except QPMInspectionError as error:
        allocations = []
        errors.append(str(error))
    rows = correlate(jobs, allocations)
    if args.job:
        rows = [row for row in rows if row["job_id"] == str(args.job)]
    if args.user:
        rows = [row for row in rows if row["user"] == args.user]
    payload = {"schema": SCHEMA, "jobs": rows, "errors": errors}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(table(
            ("JOBID", "USER", "STATE", "NODES", "QPU", "QSTATE"),
            (
                (
                    row["job_id"], row["user"], row["state"],
                    row["nodes"] or "-", row["qpu"] or "-", row["qstate"],
                )
                for row in rows
            ),
        ))
        for error in errors:
            print(f"qfw-squeue: {error}", file=sys.stderr)
    return 1 if errors else 0


def correlate(
    jobs: list[SlurmJob], allocations: list[QPMAllocation]
) -> list[dict[str, Any]]:
    """Join jobs and QPM summaries by cluster and canonical allocation ID."""

    job_groups: dict[tuple[str, str], list[SlurmJob]] = defaultdict(list)
    for job in jobs:
        job_groups[(job.cluster_name, canonical_job_id(job))].append(job)
    allocation_groups: dict[tuple[str, str], list[QPMAllocation]] = defaultdict(
        list
    )
    for allocation in allocations:
        if allocation.scheduler.lower() != "slurm":
            continue
        allocation_groups[
            (allocation.cluster_name, allocation.job_id)
        ].append(allocation)
    rows = []
    for (cluster_name, job_id), components in job_groups.items():
        matches = allocation_groups.get((cluster_name, job_id), [])
        if not matches and not cluster_name:
            possible = [
                values for (cluster, selected_job), values
                in allocation_groups.items()
                if selected_job == job_id
            ]
            if len(possible) == 1:
                matches = possible[0]
        qpus = sorted({item.service_id for item in matches})
        slurm_state = _combined_slurm_state(components)
        rows.append({
            "job_id": job_id,
            "user": components[0].user,
            "state": slurm_state,
            "nodes": ",".join(sorted({
                node for component in components for node in component.nodes
            })),
            "qpu": ",".join(qpus),
            "qstate": _quantum_state(slurm_state, matches),
            "cluster_name": cluster_name,
            "components": [component.job_id for component in components],
            "allocations": [
                {
                    "service_id": allocation.service_id,
                    "state": allocation.state,
                    "active_tasks": allocation.active_tasks,
                }
                for allocation in sorted(
                    matches, key=lambda item: item.service_id
                )
            ],
        })
    return sorted(rows, key=lambda row: _job_sort_key(row["job_id"]))


def canonical_job_id(job: SlurmJob) -> str:
    """Return the allocation leader for an ordinary or heterogeneous job."""

    return job.heterogeneous_job_id or job.job_id


def _combined_slurm_state(components: list[SlurmJob]) -> str:
    states = {component.state.upper() for component in components}
    priority = (
        "COMPLETING", "RUNNING", "PENDING", "SUSPENDED", "CONFIGURING"
    )
    for state in priority:
        if state in states:
            return state
    return sorted(states)[0] if states else "UNKNOWN"


def _quantum_state(
    slurm_state: str, allocations: list[QPMAllocation]
) -> str:
    if not allocations:
        return "EVALUATING" if slurm_state == "PENDING" else "UNAVAILABLE"
    if slurm_state == "COMPLETING":
        return "RELEASING"
    states = {allocation.state for allocation in allocations}
    if slurm_state == "PENDING" and states <= {"ACTIVE", "ACCEPTED"}:
        return "ACCEPTED"
    if states <= {"ACTIVE", "ACCEPTED"}:
        return "ACTIVE"
    return ",".join(sorted(states))


def _job_sort_key(value: str) -> tuple[int, str]:
    try:
        return int(value), value
    except ValueError:
        return sys.maxsize, value


if __name__ == "__main__":
    from .bootstrap import finish_defw_command

    finish_defw_command(main())
