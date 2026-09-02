"""Isolated deterministic gateway process for native system tests."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import time
from pathlib import Path

from qfw_slurm_gateway.config import GatewayConfig
from qfw_slurm_gateway.defw_client import QFwAdapterError, QPMBinding
from qfw_slurm_gateway.journal import Journal
from qfw_slurm_gateway.protocol import (
    AdmissionDecision,
    ReserveRequest,
    ServiceResult,
)
from qfw_slurm_gateway.server import GatewayServer
from qfw_slurm_gateway.service import GatewayService
from qfw_slurm_gateway.slurm_verifier import (
    SlurmVerifier,
    SlurmVerificationError,
    VerifiedJob,
)


class DeterministicVerifier:
    """Accept exactly the allocation identity selected by the harness."""

    def __init__(self, cluster: str, job_id: int, uid: int, gid: int):
        self.cluster = cluster
        self.job_id = job_id
        self.uid = uid
        self.gid = gid

    def _verify_identity(self, request, sender_uid: int) -> None:
        if (
            request.cluster_name != self.cluster
            or request.canonical_job_id != self.job_id
            or sender_uid not in {0, self.uid}
        ):
            raise SlurmVerificationError("deterministic job identity mismatch")

    def _job(self, state: str) -> VerifiedJob:
        return VerifiedJob(
            self.cluster,
            self.job_id,
            self.uid,
            self.gid,
            "test-user",
            "test-account",
            "test-qos",
            1,
            state,
        )

    def verify_reserve(
        self, request: ReserveRequest, sender_uid: int
    ) -> VerifiedJob:
        self._verify_identity(request, sender_uid)
        if request.job_uid != self.uid or request.job_gid != self.gid:
            raise SlurmVerificationError("deterministic owner mismatch")
        return self._job("RUNNING")

    def verify_release(self, request, sender_uid: int, expected_uid: int | None):
        self._verify_identity(request, sender_uid)
        if expected_uid is not None and expected_uid != self.uid:
            raise SlurmVerificationError("deterministic journal owner mismatch")
        return self._job("COMPLETING")


class DeterministicAdapter:
    """Return one protected, process-selected QPM behavior."""

    def __init__(self, mode: str, timeout_seconds: float):
        self.mode = mode
        self.timeout_seconds = timeout_seconds
        self._reservation_ids: dict[str, int] = {}
        self._held_by: int | None = None

    def resolve(self, service_id: str) -> QPMBinding:
        return QPMBinding(service_id, f"test-runtime-{service_id}", 1, self)

    def reserve(self, binding, request, job) -> ServiceResult:
        del request
        if self.mode in {"qpm-failure", "malformed-qpm"}:
            detail = (
                "configured QPM operation failure"
                if self.mode == "qpm-failure"
                else "configured malformed QPM result"
            )
            raise QFwAdapterError(detail)
        if self.mode == "timeout":
            time.sleep(self.timeout_seconds * 2)
            raise QFwAdapterError("configured QPM deadline expiry")
        decision = {
            "delayed": AdmissionDecision.DELAYED,
            "rejected": AdmissionDecision.REJECTED,
        }.get(self.mode, AdmissionDecision.ACCEPTED)
        if self.mode == "capacity-one":
            decision = (
                AdmissionDecision.DELAYED
                if self._held_by not in {None, job.canonical_job_id}
                else AdmissionDecision.ACCEPTED
            )
            if decision == AdmissionDecision.ACCEPTED:
                self._held_by = job.canonical_job_id
        reservation_id = None
        if decision == AdmissionDecision.ACCEPTED:
            key = f"{job.canonical_job_id}:{binding.service_id}"
            reservation_id = self._reservation_ids.setdefault(
                key, 40 + len(self._reservation_ids) + 1
            )
        return ServiceResult(
            binding.service_id,
            decision,
            0 if decision == AdmissionDecision.ACCEPTED else 9,
            reservation_id=reservation_id,
            qpm_runtime_id=binding.runtime_id,
            qpm_generation=binding.generation,
            diagnostic=(
                None
                if decision == AdmissionDecision.ACCEPTED
                else f"configured {decision.name.lower()} admission"
            ),
        )

    def evaluate(self, binding, request, job) -> ServiceResult:
        del request, job
        if self.mode in {"qpm-failure", "malformed-qpm"}:
            detail = (
                "configured QPM operation failure"
                if self.mode == "qpm-failure"
                else "configured malformed QPM result"
            )
            raise QFwAdapterError(detail)
        if self.mode == "timeout":
            time.sleep(self.timeout_seconds * 2)
            raise QFwAdapterError("configured QPM deadline expiry")
        decision = {
            "delayed": AdmissionDecision.DELAYED,
            "rejected": AdmissionDecision.REJECTED,
        }.get(self.mode, AdmissionDecision.ACCEPTED)
        return ServiceResult(
            binding.service_id,
            decision,
            0 if decision == AdmissionDecision.ACCEPTED else 9,
            qpm_runtime_id=binding.runtime_id,
            qpm_generation=binding.generation,
            diagnostic=(
                None
                if decision == AdmissionDecision.ACCEPTED
                else f"configured {decision.name.lower()} evaluation"
            ),
        )

    def release(self, binding, reservation_id: int, reason: int):
        del binding, reservation_id, reason
        if self.mode == "release-unresolved":
            raise QFwAdapterError("configured unresolved release")
        if self.mode == "capacity-one":
            self._held_by = None
        return {"status": "released"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qfw-slurm-test-gateway")
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--job-id", type=int, default=0)
    parser.add_argument("--uid", required=True, type=int)
    parser.add_argument("--gid", required=True, type=int)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    parser.add_argument(
        "--mode",
        choices=(
            "accepted",
            "delayed",
            "rejected",
            "qpm-failure",
            "malformed-qpm",
            "timeout",
            "release-unresolved",
            "capacity-one",
        ),
        default="accepted",
    )
    parser.add_argument("--live-slurm", action="store_true")
    return parser


async def _serve(args) -> None:
    config = GatewayConfig(
        listen_host="127.0.0.1",
        listen_port=args.port,
        accepted_uids=frozenset({os.getuid(), 990}),
        cluster_name=args.cluster,
        journal_path=args.journal,
        qfw_activation=Path("/test-only/qfw-activate"),
        qfw_venv=Path("/test-only/venv"),
        qfw_site_config=Path("/test-only/site.yaml"),
        request_timeout_seconds=args.timeout_seconds,
        connect_timeout_seconds=args.timeout_seconds,
    )
    journal = Journal(config.journal_path)
    verifier = (
        SlurmVerifier(args.cluster, trusted_sender_uids=frozenset({0, 990}))
        if args.live_slurm
        else DeterministicVerifier(
            args.cluster, args.job_id, args.uid, args.gid
        )
    )
    service = GatewayService(
        journal, verifier, DeterministicAdapter(args.mode, args.timeout_seconds)
    )
    server = GatewayServer(config, service)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    await server.start()
    assert server._server is not None
    port = server._server.sockets[0].getsockname()[1]
    args.ready_file.write_text(
        json.dumps(
            {
                "schema": "qfw-slurm-test-gateway-v1",
                "mode": args.mode,
                "port": port,
            }
        ),
        encoding="utf-8",
    )
    args.ready_file.chmod(0o600)
    try:
        await stopped.wait()
    finally:
        await server.close()
        journal.close()


def main() -> int:
    args = _parser().parse_args()
    if (not args.live_slurm and args.job_id <= 0) or args.uid < 0 or args.gid < 0:
        raise SystemExit("test allocation identity is invalid")
    if args.timeout_seconds <= 0:
        raise SystemExit("test timeout must be positive")
    asyncio.run(_serve(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
