"""Combined Slurm host and QPM service status."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .models import QPMService, SlurmNode
from .qpm import QPMInspectionClient, QPMInspectionError
from .render import table
from .slurm import SlurmCommandError, SlurmJsonClient


SCHEMA = "qfw-sinfo-v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qfw-sinfo",
        description="show Slurm host state alongside QPM service state",
    )
    parser.add_argument("node", nargs="?", help="show one service node")
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
    args = _parser().parse_args(arguments)
    if args.timeout <= 0:
        _parser().error("--timeout must be positive")
    errors = []
    try:
        nodes = SlurmJsonClient(args.timeout).nodes()
    except SlurmCommandError as error:
        nodes = []
        errors.append(str(error))
    try:
        services = QPMInspectionClient.connect(args.timeout).services()
    except QPMInspectionError as error:
        services = []
        errors.append(str(error))
    services = _merge_configured_services(
        services, _configured_services(args.site_config, nodes)
    )
    rows = _rows(nodes, services)
    if args.node:
        rows = [row for row in rows if row["node"] == args.node]
        if not rows:
            raise SystemExit(f"qfw-sinfo: unknown service node {args.node!r}")
    payload = {"schema": SCHEMA, "services": rows, "errors": errors}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.node:
        print(_detail(rows[0]))
        _print_errors(errors)
    else:
        print(table(
            ("NODE", "SERVICE", "BACKEND", "STATE", "ACTIVE", "SLURM_STATE"),
            (
                (
                    row["node"], row["service_id"], row["backend"],
                    row["state"], row["active_reservations"],
                    row["slurm_state"],
                )
                for row in rows
            ),
        ))
        _print_errors(errors)
    return 1 if errors else 0


def _rows(
    nodes: list[SlurmNode], services: list[QPMService]
) -> list[dict[str, Any]]:
    node_by_name = {node.name: node for node in nodes}
    rows = []
    for service in services:
        hosts = service.assigned_hosts or ("-",)
        for host in hosts:
            node = node_by_name.get(host)
            rows.append({
                "node": host,
                "service_id": service.service_id,
                "backend": service.backend,
                "state": (
                    service.state
                    if service.ready or service.state == "MAINT" else "DOWN"
                ),
                "active_reservations": service.active_reservations,
                "active_tasks": service.active_tasks,
                "slurm_state": node.state if node else "UNKNOWN",
                "partition": node.partition if node else "",
                "features": list(node.features) if node else [],
                "runtime_id": service.runtime_id,
                "generation": service.generation,
                "assigned_hosts": list(service.assigned_hosts),
                "dvm_ready": service.dvm_ready,
                "error": service.error,
            })
    return sorted(rows, key=lambda row: (row["node"], row["service_id"]))


def _configured_services(
    explicit_site: str | None, nodes: list[SlurmNode] | None = None
) -> list[QPMService]:
    if nodes:
        configured = _configured_from_nodes(nodes)
        if configured:
            return configured
    try:
        from qfw_runtime.config import (
            expand_config_value,
            load_service_manifest,
            load_yaml,
            resolve_site_config,
            site_service_config,
        )

        site_path = resolve_site_config(explicit_site)
        site = load_yaml(site_path)
        config = site_service_config(site, site_config_path=site_path)
        manifest = load_service_manifest(config["manifest"])
    except Exception:
        return _configured_from_nodes(nodes or [])
    services = []
    for item in manifest:
        hosts = str(expand_config_value(item.get("assigned-hosts") or ""))
        services.append(QPMService(
            service_id=str(item["name"]),
            runtime_id="",
            generation=0,
            backend=str(item.get("device-id") or item["name"]),
            state="DOWN",
            ready=False,
            active_reservations=0,
            active_tasks=0,
            assigned_hosts=tuple(
                host for host in hosts.replace(",", " ").split() if host
            ),
            error="service is not registered",
        ))
    return services or _configured_from_nodes(nodes or [])


def _configured_from_nodes(nodes: list[SlurmNode]) -> list[QPMService]:
    grouped: dict[str, list[str]] = {}
    for node in nodes:
        service_ids = [
            feature.removeprefix("qpm-")
            for feature in node.features
            if feature.startswith("qpm-")
        ]
        for service_id in service_ids:
            grouped.setdefault(service_id, []).append(node.name)
    return [
        QPMService(
            service_id=service_id,
            runtime_id="",
            generation=0,
            backend=service_id,
            state="DOWN",
            ready=False,
            active_reservations=0,
            active_tasks=0,
            assigned_hosts=tuple(sorted(hosts)),
            error="service is not registered",
        )
        for service_id, hosts in sorted(grouped.items())
    ]


def _merge_configured_services(
    active: list[QPMService], configured: list[QPMService]
) -> list[QPMService]:
    service_ids = {service.service_id for service in active}
    return active + [
        service for service in configured
        if service.service_id not in service_ids
    ]


def _detail(row: dict[str, Any]) -> str:
    fields = (
        ("Node", row["node"]),
        ("Service", row["service_id"]),
        ("Backend", row["backend"]),
        ("QPM state", row["state"]),
        ("Slurm state", row["slurm_state"]),
        ("Runtime ID", row["runtime_id"] or "-"),
        ("Generation", row["generation"] or "-"),
        ("Active reservations", row["active_reservations"]),
        ("Active tasks", row["active_tasks"]),
        ("Assigned hosts", ",".join(row["assigned_hosts"]) or "-"),
        ("DVM ready", _optional_bool(row["dvm_ready"])),
        ("Features", ",".join(row["features"]) or "-"),
        ("Error", row["error"] or "-"),
    )
    width = max(len(name) for name, _value in fields)
    return "\n".join(f"{name.ljust(width)}  {value}" for name, value in fields)


def _optional_bool(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def _print_errors(errors: list[str]) -> None:
    for error in errors:
        print(f"qfw-sinfo: {error}", file=sys.stderr)


if __name__ == "__main__":
    from .bootstrap import finish_defw_command

    finish_defw_command(main())
