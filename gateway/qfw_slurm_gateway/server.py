"""Bounded asynchronous QSGP server."""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from typing import Protocol

from .authentication import AuthenticationError, MungeAuthenticator
from .config import GatewayConfig
from .protocol import (
    ErrorResponse,
    GatewayError,
    ProtocolError,
    bounded_diagnostic,
    decode_header,
    decode_request,
    encode_response,
)
from .service import GatewayService


LOG = logging.getLogger(__name__)
LENGTH = struct.Struct("!I")


class Authenticator(Protocol):
    def decode(self, credential: bytes): ...

    def encode(self, payload: bytes) -> bytes: ...


class GatewayServer:
    def __init__(
        self,
        config: GatewayConfig,
        service: GatewayService,
        authenticator: Authenticator | None = None,
    ):
        self.config = config
        self.service = service
        self.authenticator = authenticator or MungeAuthenticator()
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._client,
            self.config.listen_host,
            self.config.listen_port,
            limit=self.config.max_credential_size + LENGTH.size,
        )

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        clients = set(self._clients)
        if not clients:
            return
        _done, pending = await asyncio.wait(
            clients, timeout=self.config.request_timeout_seconds
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._clients.add(task)
        peer = writer.get_extra_info("peername")
        started_ns = time.monotonic_ns()
        try:
            credential = await asyncio.wait_for(
                self._read_credential(reader),
                timeout=self.config.request_timeout_seconds,
            )
            identity, frame = self.authenticator.decode(credential)
            if identity.uid not in self.config.accepted_uids:
                raise AuthenticationError("MUNGE UID is not authorized")
            if len(frame) > self.config.max_frame_size:
                raise ProtocolError("QSGP frame exceeds configured maximum")
            try:
                header, request = decode_request(frame)
                response = await asyncio.wait_for(
                    self.service.handle(request, identity.uid),
                    timeout=self.config.request_timeout_seconds,
                )
                LOG.info(
                    "operation=%s cluster=%s job_id=%s request_id=%s "
                    "correlation_id=%s sender_uid=%s response=%s duration_ns=%s",
                    type(request).__name__,
                    request.cluster_name,
                    request.canonical_job_id,
                    request.request_id,
                    header.correlation_id,
                    identity.uid,
                    type(response).__name__,
                    time.monotonic_ns() - started_ns,
                )
            except ProtocolError as error:
                header = decode_header(frame)
                response = ErrorResponse(
                    GatewayError.INVALID_REQUEST,
                    None,
                    bounded_diagnostic(error),
                )
            response_frame = encode_response(response, header.correlation_id)
            response_credential = self.authenticator.encode(response_frame)
            await asyncio.wait_for(
                self._write_credential(writer, response_credential),
                timeout=self.config.request_timeout_seconds,
            )
        except (AuthenticationError, asyncio.IncompleteReadError) as error:
            LOG.warning("dropping unauthenticated QSGP peer %r: %s", peer, error)
        except (ProtocolError, asyncio.TimeoutError, OSError) as error:
            LOG.warning("QSGP request from %r failed: %s", peer, error)
        except Exception:
            LOG.exception("unexpected QSGP request failure from %r", peer)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            if task is not None:
                self._clients.discard(task)

    async def _read_credential(self, reader: asyncio.StreamReader) -> bytes:
        encoded_size = await reader.readexactly(LENGTH.size)
        size = LENGTH.unpack(encoded_size)[0]
        if size == 0 or size > self.config.max_credential_size:
            raise ProtocolError("QSGP credential size is invalid")
        return await reader.readexactly(size)

    async def _write_credential(
        self, writer: asyncio.StreamWriter, credential: bytes
    ) -> None:
        if not credential or len(credential) > self.config.max_credential_size:
            raise ProtocolError("encoded response credential size is invalid")
        writer.write(LENGTH.pack(len(credential)) + credential)
        await writer.drain()
