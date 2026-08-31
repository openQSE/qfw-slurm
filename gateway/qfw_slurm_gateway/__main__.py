"""Administrative entry point for the qfw-slurm gateway."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import time

from .config import load_config
from .defw_client import QFwAdapter
from .journal import Journal
from .protocol import ReleaseRequest, response_to_dict
from .server import GatewayServer
from .service import GatewayService
from .slurm_verifier import SlurmVerifier


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qfw-slurm-gateway")
    parser.add_argument(
        "--config", required=True, help="gateway YAML configuration"
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("serve", help="run the QSGP gateway")
    status = subcommands.add_parser("status", help="inspect one allocation")
    status.add_argument("job_id", type=int)
    subcommands.add_parser("list", help="list nonterminal allocations")
    retry = subcommands.add_parser(
        "retry-release", help="retry release for one allocation"
    )
    retry.add_argument("job_id", type=int)
    retry.add_argument("--reason", type=int, default=0)
    retry.add_argument("--request-id", type=int)
    check = subcommands.add_parser(
        "check", help="check journal, directory, and configured QPMs"
    )
    check.add_argument("--service", action="append", default=[])
    return parser


def _notify_service_manager(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
        notify.connect(address)
        notify.sendall(message.encode("utf-8"))


async def _serve(config) -> None:
    journal = Journal(config.journal_path)
    adapter = QFwAdapter(
        str(config.qfw_site_config),
        config.connect_timeout_seconds,
        str(config.qfw_activation),
        str(config.qfw_venv),
    )
    adapter.start()
    verifier = SlurmVerifier(
        config.cluster_name,
        config.slurm_command,
        config.connect_timeout_seconds,
        config.accepted_uids,
    )
    server = GatewayServer(
        config, GatewayService(journal, verifier, adapter)
    )
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    await server.start()
    _notify_service_manager("READY=1\nSTATUS=QSGP listener is ready")
    try:
        await stopped.wait()
    finally:
        _notify_service_manager("STOPPING=1\nSTATUS=QSGP listener is stopping")
        await server.close()
        journal.close()


async def _retry(config, args) -> dict:
    journal = Journal(config.journal_path)
    adapter = QFwAdapter(
        str(config.qfw_site_config),
        config.connect_timeout_seconds,
        str(config.qfw_activation),
        str(config.qfw_venv),
    )
    adapter.start()
    verifier = SlurmVerifier(
        config.cluster_name,
        config.slurm_command,
        config.connect_timeout_seconds,
        config.accepted_uids,
    )
    service = GatewayService(journal, verifier, adapter)
    request_id = args.request_id or (time.time_ns() & ((1 << 64) - 1))
    if request_id == 0:
        request_id = 1
    try:
        response = await service.retry_release(
            ReleaseRequest(
                request_id,
                config.cluster_name,
                args.job_id,
                args.reason,
            )
        )
        return response_to_dict(response)
    finally:
        journal.close()


async def _check(config, services: list[str]) -> dict:
    journal = Journal(config.journal_path)
    adapter = QFwAdapter(
        str(config.qfw_site_config),
        config.connect_timeout_seconds,
        str(config.qfw_activation),
        str(config.qfw_venv),
    )
    try:
        adapter.start()
        resolved = []
        for service_id in services:
            binding = await asyncio.to_thread(adapter.resolve, service_id)
            resolved.append(
                {
                    "service_id": binding.service_id,
                    "runtime_id": binding.runtime_id,
                    "generation": str(binding.generation),
                }
            )
        return {"status": "ready", "services": resolved}
    finally:
        journal.close()


def main() -> int:
    args = _parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config(args.config)
    if args.command == "serve":
        asyncio.run(_serve(config))
        return 0
    if args.command == "retry-release":
        print(json.dumps(asyncio.run(_retry(config, args)), indent=2, sort_keys=True))
        return 0
    if args.command == "check":
        print(
            json.dumps(
                asyncio.run(_check(config, args.service)),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    journal = Journal(config.journal_path)
    try:
        result = (
            journal.nonterminal_allocations()
            if args.command == "list"
            else journal.allocation_status(config.cluster_name, args.job_id)
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        journal.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
