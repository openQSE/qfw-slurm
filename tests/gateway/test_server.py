from __future__ import annotations

import asyncio
import dataclasses
import struct
from pathlib import Path

from qfw_slurm_gateway.authentication import PeerIdentity
from qfw_slurm_gateway.config import GatewayConfig
from qfw_slurm_gateway.protocol import (
    AdmissionDecision,
    MessageType,
    ReserveRequest,
    ReserveResponse,
    ServiceResult,
    Workload,
    WorkloadKind,
    decode_header,
    encode_reserve_request,
)
from qfw_slurm_gateway.server import GatewayServer


class PassthroughAuthenticator:
    def decode(self, credential):
        return PeerIdentity(0, 0), credential

    def encode(self, payload):
        return payload


class FakeService:
    async def handle(self, request, sender_uid):
        assert sender_uid == 0
        return ReserveResponse(
            request.request_id,
            AdmissionDecision.ACCEPTED,
            (
                ServiceResult(
                    request.service_ids[0],
                    AdmissionDecision.ACCEPTED,
                    0,
                    reservation_id=41,
                ),
            ),
        )


def config(tmp_path) -> GatewayConfig:
    return GatewayConfig(
        listen_host="127.0.0.1",
        listen_port=0,
        accepted_uids=frozenset({0}),
        cluster_name="cluster",
        journal_path=tmp_path / "journal.db",
        qfw_activation=Path("/qfw-activate"),
        qfw_venv=Path("/venv"),
        qfw_site_config=Path("/site.yaml"),
        request_timeout_seconds=2,
    )


def test_one_request_per_connection(tmp_path) -> None:
    asyncio.run(_one_request_per_connection(tmp_path))


async def _one_request_per_connection(tmp_path) -> None:
    server = GatewayServer(
        config(tmp_path), FakeService(), PassthroughAuthenticator()
    )
    await server.start()
    try:
        port = server._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        request = ReserveRequest(
            5,
            "cluster",
            10,
            0,
            0,
            Workload(WorkloadKind.QUANTUM, 1, 1, 1, 1, 1),
            ("nwqsim",),
        )
        frame = encode_reserve_request(request, 99)
        writer.write(struct.pack("!I", len(frame)) + frame)
        await writer.drain()
        size = struct.unpack("!I", await reader.readexactly(4))[0]
        response = await reader.readexactly(size)
        header = decode_header(response)
        assert header.message_type == MessageType.RESERVE_RESPONSE
        assert header.correlation_id == 99
        assert await reader.read() == b""
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


def test_close_cancels_stalled_client_after_bound(tmp_path) -> None:
    asyncio.run(_close_cancels_stalled_client_after_bound(tmp_path))


async def _close_cancels_stalled_client_after_bound(tmp_path) -> None:
    selected = dataclasses.replace(
        config(tmp_path), request_timeout_seconds=0.01
    )
    server = GatewayServer(
        selected, FakeService(), PassthroughAuthenticator()
    )
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    _reader, writer = await asyncio.open_connection("127.0.0.1", port)
    await asyncio.sleep(0)

    await asyncio.wait_for(server.close(), timeout=1)

    writer.close()
    await writer.wait_closed()
