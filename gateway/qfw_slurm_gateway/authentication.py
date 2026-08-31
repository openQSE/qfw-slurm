"""MUNGE authentication for the native QSGP socket."""

from __future__ import annotations

import ctypes
import ctypes.util
import dataclasses


class AuthenticationError(RuntimeError):
    """Raised when a QSGP credential cannot be authenticated."""


@dataclasses.dataclass(frozen=True)
class PeerIdentity:
    uid: int
    gid: int


class MungeAuthenticator:
    """Small ctypes binding that avoids a gateway-time compiler dependency."""

    def __init__(self, library: str | None = None):
        name = library or ctypes.util.find_library("munge")
        if not name:
            raise AuthenticationError("libmunge is not installed")
        self._library = ctypes.CDLL(name)
        self._configure()

    def _configure(self) -> None:
        library = self._library
        library.munge_encode.argtypes = [
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        library.munge_encode.restype = ctypes.c_int
        library.munge_decode.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
        library.munge_decode.restype = ctypes.c_int
        library.munge_strerror.argtypes = [ctypes.c_int]
        library.munge_strerror.restype = ctypes.c_char_p
        self._free = ctypes.CDLL(None).free
        self._free.argtypes = [ctypes.c_void_p]

    def _error(self, code: int) -> AuthenticationError:
        detail = self._library.munge_strerror(code)
        message = detail.decode("utf-8", "replace") if detail else str(code)
        return AuthenticationError(f"MUNGE operation failed: {message}")

    def encode(self, payload: bytes) -> bytes:
        if not isinstance(payload, bytes):
            raise AuthenticationError("MUNGE payload must be bytes")
        credential = ctypes.c_char_p()
        buffer = ctypes.create_string_buffer(payload) if payload else None
        code = self._library.munge_encode(
            ctypes.byref(credential),
            None,
            ctypes.cast(buffer, ctypes.c_void_p) if buffer is not None else None,
            len(payload),
        )
        if code != 0:
            raise self._error(code)
        try:
            return ctypes.string_at(credential)
        finally:
            self._free(credential)

    def decode(self, credential: bytes) -> tuple[PeerIdentity, bytes]:
        if not credential or b"\0" in credential:
            raise AuthenticationError("MUNGE credential framing is invalid")
        payload = ctypes.c_void_p()
        payload_size = ctypes.c_int()
        uid = ctypes.c_uint()
        gid = ctypes.c_uint()
        code = self._library.munge_decode(
            credential,
            None,
            ctypes.byref(payload),
            ctypes.byref(payload_size),
            ctypes.byref(uid),
            ctypes.byref(gid),
        )
        if code != 0:
            raise self._error(code)
        try:
            decoded = ctypes.string_at(payload, payload_size.value)
        finally:
            if payload.value:
                self._free(payload)
        return PeerIdentity(uid.value, gid.value), decoded
