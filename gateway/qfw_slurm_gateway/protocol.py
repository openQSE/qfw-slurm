"""QSGP version 1 wire encoding shared with the native Slurm components."""

from __future__ import annotations

import dataclasses
import enum
import struct
from typing import Iterable


MAGIC = b"QSGP"
VERSION_MAJOR = 1
VERSION_MINOR = 0
HEADER_SIZE = 32
MAX_FRAME_SIZE = 256 * 1024
MAX_CREDENTIAL_SIZE = 512 * 1024
MAX_CLUSTER_NAME = 128
MAX_SERVICE_ID = 256
MAX_DIAGNOSTIC = 4096
MAX_SERVICES = 32
TLV_REQUIRED = 1

HEADER = struct.Struct("!4sHHHHIQII")
TLV_HEADER = struct.Struct("!HHI")


class ProtocolError(ValueError):
    """Raised when a QSGP frame violates the versioned contract."""


class MessageType(enum.IntEnum):
    RESERVE_REQUEST = 0x0001
    RELEASE_REQUEST = 0x0002
    EVALUATE_REQUEST = 0x0003
    GET_RESERVATIONS_REQUEST = 0x0004
    RESERVE_RESPONSE = 0x8001
    RELEASE_RESPONSE = 0x8002
    EVALUATE_RESPONSE = 0x8003
    GET_RESERVATIONS_RESPONSE = 0x8004
    ERROR_RESPONSE = 0x8FFF


class Field(enum.IntEnum):
    CLUSTER_NAME = 0x0001
    CANONICAL_JOB_ID = 0x0002
    HETERO_JOB_ID = 0x0003
    HETERO_COMPONENT = 0x0004
    JOB_UID = 0x0005
    JOB_GID = 0x0006
    SERVICE_ID = 0x0007
    WORKLOAD_KIND = 0x0008
    WALLTIME_NS = 0x0009
    CIRCUIT_COUNT = 0x000A
    MAX_QUBITS = 0x000B
    MAX_DEPTH = 0x000C
    MAX_SHOTS = 0x000D
    MAX_ONE_Q_GATES = 0x000E
    MAX_TWO_Q_GATES = 0x000F
    MAX_MEASUREMENTS = 0x0010
    RESERVATION_ID = 0x0011
    RELEASE_REASON = 0x0012
    ADMISSION_DECISION = 0x0013
    REASON_CODE = 0x0014
    RETRY_AFTER_NS = 0x0015
    DIAGNOSTIC = 0x0016
    QPM_RUNTIME_ID = 0x0017
    QPM_GENERATION = 0x0018
    RESERVATION_STATE = 0x0019
    REQUEST_ID = 0x001A
    SERVICE_REQUEST = 0x001B
    ESTIMATED_START_NS = 0x001C
    ESTIMATED_FINISH_NS = 0x001D
    GATEWAY_ERROR_CODE = 0x001E
    SERVICE_RESULT = 0x001F
    RELEASE_RESULT = 0x0020
    OBSERVED_JOB_ID = 0x0021
    RESERVATION = 0x0022


class WorkloadKind(enum.IntEnum):
    QUANTUM = 1
    HYBRID = 2


class AdmissionDecision(enum.IntEnum):
    ACCEPTED = 1
    DELAYED = 2
    REJECTED = 3


class ReservationState(enum.IntEnum):
    RELEASED = 1
    ALREADY_TERMINAL = 2
    NOT_FOUND = 3
    STALE_RUNTIME = 4
    AUTHORIZATION_FAILURE = 5
    QPM_FAILURE = 6
    GATEWAY_FAILURE = 7


class GatewayError(enum.IntEnum):
    INVALID_REQUEST = 1
    UNAUTHORIZED = 2
    DIRECTORY = 3
    QPM = 4
    TIMEOUT = 5
    INTERNAL = 6
    REQUEST_CONFLICT = 7
    UNSUPPORTED_VERSION = 8
    ALLOCATION_NOT_FOUND = 9
    ALLOCATION_NOT_ACCEPTED = 10
    ALLOCATION_RELEASED = 11


@dataclasses.dataclass(frozen=True)
class Header:
    message_type: MessageType
    correlation_id: int
    payload_size: int
    major_version: int = VERSION_MAJOR
    minor_version: int = VERSION_MINOR


@dataclasses.dataclass(frozen=True)
class Workload:
    kind: WorkloadKind
    walltime_ns: int
    circuit_count: int
    max_qubits: int
    max_depth: int
    max_shots: int
    max_one_q_gates: int | None = None
    max_two_q_gates: int | None = None
    max_measurements: int | None = None


@dataclasses.dataclass(frozen=True)
class ReserveRequest:
    request_id: int
    cluster_name: str
    canonical_job_id: int
    job_uid: int
    job_gid: int
    workload: Workload | None
    service_ids: tuple[str, ...]
    hetero_job_id: int | None = None
    hetero_component: int | None = None


@dataclasses.dataclass(frozen=True)
class EvaluateRequest(ReserveRequest):
    """Non-binding admission request using the reserve workload envelope."""


@dataclasses.dataclass(frozen=True)
class ServiceResult:
    service_id: str
    decision: AdmissionDecision
    reason_code: int
    reservation_id: int | None = None
    retry_after_ns: int | None = None
    estimated_start_ns: int | None = None
    estimated_finish_ns: int | None = None
    qpm_runtime_id: str | None = None
    qpm_generation: int | None = None
    diagnostic: str | None = None


@dataclasses.dataclass(frozen=True)
class ReserveResponse:
    request_id: int
    decision: AdmissionDecision
    results: tuple[ServiceResult, ...]


@dataclasses.dataclass(frozen=True)
class EvaluateResponse(ReserveResponse):
    """Admission estimate whose results never contain reservation IDs."""


@dataclasses.dataclass(frozen=True)
class ReleaseRequest:
    request_id: int
    cluster_name: str
    canonical_job_id: int
    reason: int


@dataclasses.dataclass(frozen=True)
class ReleaseResult:
    service_id: str
    reservation_id: int
    state: ReservationState
    gateway_error: GatewayError | None = None
    diagnostic: str | None = None


@dataclasses.dataclass(frozen=True)
class ReleaseResponse:
    request_id: int
    results: tuple[ReleaseResult, ...]


@dataclasses.dataclass(frozen=True)
class GetReservationsRequest:
    request_id: int
    cluster_name: str
    observed_job_id: int
    job_uid: int
    job_gid: int


@dataclasses.dataclass(frozen=True)
class Reservation:
    service_id: str
    reservation_id: int


@dataclasses.dataclass(frozen=True)
class GetReservationsResponse:
    request_id: int
    canonical_job_id: int
    reservations: tuple[Reservation, ...]


@dataclasses.dataclass(frozen=True)
class ErrorResponse:
    error_code: GatewayError
    request_id: int | None = None
    diagnostic: str | None = None


def bounded_diagnostic(value: object) -> str:
    """Return valid UTF-8 that fits the QSGP diagnostic field."""

    encoded = str(value).encode("utf-8", "replace")
    if len(encoded) <= MAX_DIAGNOSTIC:
        return encoded.decode("utf-8")
    return encoded[:MAX_DIAGNOSTIC].decode("utf-8", "ignore")


def _uint(value: int, bits: int, label: str, *, nonzero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{label} must be an unsigned integer")
    if value < 0 or value >= 1 << bits or (nonzero and value == 0):
        raise ProtocolError(f"{label} is outside uint{bits}")
    return value


def _string(value: str, maximum: int, label: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{label} must be a nonempty string")
    encoded = value.encode("utf-8")
    if len(encoded) > maximum or b"\0" in encoded:
        raise ProtocolError(f"{label} exceeds its wire bound")
    return encoded


def _tlv(field: Field, value: bytes, *, required: bool = True) -> bytes:
    flags = TLV_REQUIRED if required else 0
    padding = (-len(value)) % 4
    return TLV_HEADER.pack(field, flags, len(value)) + value + bytes(padding)


def _u32(field: Field, value: int) -> bytes:
    return _tlv(field, struct.pack("!I", _uint(value, 32, field.name)))


def _u64(field: Field, value: int) -> bytes:
    return _tlv(field, struct.pack("!Q", _uint(value, 64, field.name)))


def _text(field: Field, value: str, maximum: int) -> bytes:
    return _tlv(field, _string(value, maximum, field.name))


def _frame(message_type: MessageType, correlation_id: int, payload: bytes) -> bytes:
    _uint(correlation_id, 64, "correlation_id", nonzero=True)
    if len(payload) > MAX_FRAME_SIZE - HEADER_SIZE:
        raise ProtocolError("QSGP payload is too large")
    return HEADER.pack(
        MAGIC,
        VERSION_MAJOR,
        VERSION_MINOR,
        message_type,
        0,
        HEADER_SIZE,
        correlation_id,
        len(payload),
        0,
    ) + payload


def decode_header(frame: bytes) -> Header:
    if not isinstance(frame, bytes) or not HEADER_SIZE <= len(frame) <= MAX_FRAME_SIZE:
        raise ProtocolError("QSGP frame size is invalid")
    magic, major, minor, kind, flags, header_size, correlation, size, reserved = (
        HEADER.unpack_from(frame)
    )
    if magic != MAGIC:
        raise ProtocolError("QSGP magic is invalid")
    if major != VERSION_MAJOR or minor > VERSION_MINOR:
        raise ProtocolError("QSGP version is unsupported")
    if flags != 0 or header_size != HEADER_SIZE or reserved != 0:
        raise ProtocolError("QSGP header contains unsupported fields")
    _uint(correlation, 64, "correlation_id", nonzero=True)
    if size != len(frame) - HEADER_SIZE:
        raise ProtocolError("QSGP payload size does not match the frame")
    try:
        message_type = MessageType(kind)
    except ValueError as error:
        raise ProtocolError("QSGP message type is unsupported") from error
    return Header(message_type, correlation, size, major, minor)


def _tlvs(payload: bytes) -> Iterable[tuple[Field | None, int, bytes]]:
    offset = 0
    while offset < len(payload):
        if len(payload) - offset < TLV_HEADER.size:
            raise ProtocolError("truncated QSGP TLV header")
        raw_type, flags, length = TLV_HEADER.unpack_from(payload, offset)
        offset += TLV_HEADER.size
        if flags & ~TLV_REQUIRED:
            raise ProtocolError("QSGP TLV flags are unsupported")
        padding = (-length) % 4
        end = offset + length
        padded_end = end + padding
        if end < offset or padded_end > len(payload):
            raise ProtocolError("truncated QSGP TLV value")
        if any(payload[end:padded_end]):
            raise ProtocolError("QSGP TLV padding must be zero")
        try:
            field = Field(raw_type)
        except ValueError:
            if flags & TLV_REQUIRED:
                raise ProtocolError("unknown required QSGP field") from None
            field = None
        yield field, flags, payload[offset:end]
        offset = padded_end


def _one(
    values: dict[Field, bytes], field: Field, *, required: bool = True
) -> bytes | None:
    value = values.get(field)
    if required and value is None:
        raise ProtocolError(f"missing required field {field.name}")
    return value


def _collect(
    payload: bytes, repeated: set[Field]
) -> tuple[dict[Field, bytes], dict[Field, list[bytes]]]:
    scalar: dict[Field, bytes] = {}
    groups = {field: [] for field in repeated}
    for field, _flags, value in _tlvs(payload):
        if field is None:
            continue
        if field in repeated:
            groups[field].append(value)
        elif field in scalar:
            raise ProtocolError(f"duplicate scalar field {field.name}")
        else:
            scalar[field] = value
    return scalar, groups


def _decode_u32(value: bytes | None, label: str) -> int:
    if value is None or len(value) != 4:
        raise ProtocolError(f"{label} must contain uint32")
    return struct.unpack("!I", value)[0]


def _decode_u64(value: bytes | None, label: str) -> int:
    if value is None or len(value) != 8:
        raise ProtocolError(f"{label} must contain uint64")
    return struct.unpack("!Q", value)[0]


def _decode_text(value: bytes | None, maximum: int, label: str) -> str:
    if value is None or not value or len(value) > maximum or b"\0" in value:
        raise ProtocolError(f"{label} is not a valid bounded string")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError(f"{label} is not UTF-8") from error


def _encode_admission_request(
    request: ReserveRequest, correlation_id: int, message_type: MessageType
) -> bytes:
    _validate_reserve_request(request)
    workload = request.workload
    payload = b"".join(
        (
            _u64(Field.REQUEST_ID, request.request_id),
            _text(Field.CLUSTER_NAME, request.cluster_name, MAX_CLUSTER_NAME),
            _u64(Field.CANONICAL_JOB_ID, request.canonical_job_id),
            _u64(Field.HETERO_JOB_ID, request.hetero_job_id)
            if request.hetero_job_id is not None
            else b"",
            _u32(Field.HETERO_COMPONENT, request.hetero_component)
            if request.hetero_component is not None
            else b"",
            _u32(Field.JOB_UID, request.job_uid),
            _u32(Field.JOB_GID, request.job_gid),
            _u32(Field.WORKLOAD_KIND, workload.kind)
            if workload is not None
            else b"",
            _u64(Field.WALLTIME_NS, workload.walltime_ns)
            if workload is not None
            else b"",
            _u64(Field.CIRCUIT_COUNT, workload.circuit_count)
            if workload is not None
            else b"",
            _u32(Field.MAX_QUBITS, workload.max_qubits)
            if workload is not None
            else b"",
            _u64(Field.MAX_DEPTH, workload.max_depth)
            if workload is not None
            else b"",
            _u64(Field.MAX_SHOTS, workload.max_shots)
            if workload is not None
            else b"",
            _u64(Field.MAX_ONE_Q_GATES, workload.max_one_q_gates)
            if workload is not None and workload.max_one_q_gates is not None
            else b"",
            _u64(Field.MAX_TWO_Q_GATES, workload.max_two_q_gates)
            if workload is not None and workload.max_two_q_gates is not None
            else b"",
            _u64(Field.MAX_MEASUREMENTS, workload.max_measurements)
            if workload is not None and workload.max_measurements is not None
            else b"",
        )
    )
    for service_id in request.service_ids:
        nested = _text(Field.SERVICE_ID, service_id, MAX_SERVICE_ID)
        payload += _tlv(Field.SERVICE_REQUEST, nested)
    return _frame(message_type, correlation_id, payload)


def encode_reserve_request(request: ReserveRequest, correlation_id: int) -> bytes:
    if request.workload is None:
        raise ProtocolError("reserve request requires a workload envelope")
    return _encode_admission_request(
        request, correlation_id, MessageType.RESERVE_REQUEST
    )


def encode_evaluate_request(request: EvaluateRequest, correlation_id: int) -> bytes:
    if request.workload is None:
        raise ProtocolError("evaluate request requires a workload envelope")
    return _encode_admission_request(
        request, correlation_id, MessageType.EVALUATE_REQUEST
    )


def _decode_service_request(payload: bytes) -> str:
    values, repeated = _collect(payload, set())
    if repeated or set(values) != {Field.SERVICE_ID}:
        raise ProtocolError("service request contains invalid fields")
    return _decode_text(values[Field.SERVICE_ID], MAX_SERVICE_ID, "service_id")


def _decode_admission_request(
    frame: bytes,
    message_type: MessageType,
    request_type: type[ReserveRequest],
) -> tuple[Header, ReserveRequest]:
    header = decode_header(frame)
    if header.message_type != message_type:
        raise ProtocolError("unexpected admission request type")
    values, repeated = _collect(
        frame[HEADER_SIZE:], {Field.SERVICE_REQUEST}
    )
    allowed = {
        Field.REQUEST_ID,
        Field.CLUSTER_NAME,
        Field.CANONICAL_JOB_ID,
        Field.HETERO_JOB_ID,
        Field.HETERO_COMPONENT,
        Field.JOB_UID,
        Field.JOB_GID,
        Field.WORKLOAD_KIND,
        Field.WALLTIME_NS,
        Field.CIRCUIT_COUNT,
        Field.MAX_QUBITS,
        Field.MAX_DEPTH,
        Field.MAX_SHOTS,
        Field.MAX_ONE_Q_GATES,
        Field.MAX_TWO_Q_GATES,
        Field.MAX_MEASUREMENTS,
    }
    if set(values) - allowed:
        raise ProtocolError("reserve request contains invalid fields")
    services = tuple(
        _decode_service_request(item)
        for item in repeated[Field.SERVICE_REQUEST]
    )
    if not 0 < len(services) <= MAX_SERVICES or len(set(services)) != len(services):
        raise ProtocolError("reserve request service set is invalid")
    workload_fields = {
        Field.WORKLOAD_KIND,
        Field.WALLTIME_NS,
        Field.CIRCUIT_COUNT,
        Field.MAX_QUBITS,
        Field.MAX_DEPTH,
        Field.MAX_SHOTS,
    }
    present = workload_fields & set(values)
    if present and present != workload_fields:
        raise ProtocolError("workload envelope is incomplete")
    if not present and any(
        field in values
        for field in (
            Field.MAX_ONE_Q_GATES,
            Field.MAX_TWO_Q_GATES,
            Field.MAX_MEASUREMENTS,
        )
    ):
        raise ProtocolError("optional workload fields lack an envelope")
    workload = _decode_workload(values) if present else None
    request = request_type(
        request_id=_decode_u64(_one(values, Field.REQUEST_ID), "request_id"),
        cluster_name=_decode_text(
            _one(values, Field.CLUSTER_NAME), MAX_CLUSTER_NAME, "cluster_name"
        ),
        canonical_job_id=_decode_u64(
            _one(values, Field.CANONICAL_JOB_ID), "canonical_job_id"
        ),
        job_uid=_decode_u32(_one(values, Field.JOB_UID), "job_uid"),
        job_gid=_decode_u32(_one(values, Field.JOB_GID), "job_gid"),
        workload=workload,
        service_ids=services,
        hetero_job_id=_decode_u64(values[Field.HETERO_JOB_ID], "hetero_job_id")
        if Field.HETERO_JOB_ID in values
        else None,
        hetero_component=_decode_u32(
            values[Field.HETERO_COMPONENT], "hetero_component"
        )
        if Field.HETERO_COMPONENT in values
        else None,
    )
    _validate_reserve_request(request)
    return header, request


def decode_reserve_request(frame: bytes) -> tuple[Header, ReserveRequest]:
    header, request = _decode_admission_request(
        frame, MessageType.RESERVE_REQUEST, ReserveRequest
    )
    if request.workload is None:
        raise ProtocolError("reserve request requires a workload envelope")
    return header, request


def decode_evaluate_request(frame: bytes) -> tuple[Header, EvaluateRequest]:
    header, request = _decode_admission_request(
        frame, MessageType.EVALUATE_REQUEST, EvaluateRequest
    )
    if not isinstance(request, EvaluateRequest):
        raise ProtocolError("decoded evaluate request has the wrong type")
    if request.workload is None:
        raise ProtocolError("evaluate request requires a workload envelope")
    return header, request


def _validate_reserve_request(request: ReserveRequest) -> None:
    _uint(request.request_id, 64, "request_id", nonzero=True)
    _uint(request.canonical_job_id, 64, "canonical_job_id", nonzero=True)
    _uint(request.job_uid, 32, "job_uid")
    _uint(request.job_gid, 32, "job_gid")
    _string(request.cluster_name, MAX_CLUSTER_NAME, "cluster_name")
    if not 0 < len(request.service_ids) <= MAX_SERVICES:
        raise ProtocolError("reserve request service count is invalid")
    if len(set(request.service_ids)) != len(request.service_ids):
        raise ProtocolError("reserve request service set is invalid")
    for service_id in request.service_ids:
        _string(service_id, MAX_SERVICE_ID, "service_id")
    if (request.hetero_job_id is None) != (request.hetero_component is None):
        raise ProtocolError("heterogeneous allocation fields are incomplete")
    if request.hetero_job_id is not None:
        _uint(request.hetero_job_id, 64, "hetero_job_id", nonzero=True)
        _uint(request.hetero_component, 32, "hetero_component")
    workload = request.workload
    if workload is None:
        return
    try:
        WorkloadKind(workload.kind)
    except ValueError as error:
        raise ProtocolError("workload kind is invalid") from error
    for value, bits, label in (
        (workload.walltime_ns, 64, "walltime_ns"),
        (workload.circuit_count, 64, "circuit_count"),
        (workload.max_qubits, 32, "max_qubits"),
        (workload.max_depth, 64, "max_depth"),
        (workload.max_shots, 64, "max_shots"),
    ):
        _uint(value, bits, label, nonzero=True)
    for value, label in (
        (workload.max_one_q_gates, "max_one_q_gates"),
        (workload.max_two_q_gates, "max_two_q_gates"),
        (workload.max_measurements, "max_measurements"),
    ):
        if value is not None:
            _uint(value, 64, label)


def _decode_workload(values: dict[Field, bytes]) -> Workload:
    try:
        kind = WorkloadKind(_decode_u32(values[Field.WORKLOAD_KIND], "workload_kind"))
    except ValueError as error:
        raise ProtocolError("workload kind is invalid") from error
    return Workload(
        kind=kind,
        walltime_ns=_decode_u64(values[Field.WALLTIME_NS], "walltime_ns"),
        circuit_count=_decode_u64(values[Field.CIRCUIT_COUNT], "circuit_count"),
        max_qubits=_decode_u32(values[Field.MAX_QUBITS], "max_qubits"),
        max_depth=_decode_u64(values[Field.MAX_DEPTH], "max_depth"),
        max_shots=_decode_u64(values[Field.MAX_SHOTS], "max_shots"),
        max_one_q_gates=_decode_u64(values[Field.MAX_ONE_Q_GATES], "max_one_q_gates")
        if Field.MAX_ONE_Q_GATES in values
        else None,
        max_two_q_gates=_decode_u64(values[Field.MAX_TWO_Q_GATES], "max_two_q_gates")
        if Field.MAX_TWO_Q_GATES in values
        else None,
        max_measurements=_decode_u64(values[Field.MAX_MEASUREMENTS], "max_measurements")
        if Field.MAX_MEASUREMENTS in values
        else None,
    )


def _encode_service_result(result: ServiceResult, reserve: bool) -> bytes:
    nested = b"".join(
        (
            _text(Field.SERVICE_ID, result.service_id, MAX_SERVICE_ID),
            _u32(Field.ADMISSION_DECISION, result.decision),
            _u64(Field.REASON_CODE, result.reason_code),
            _u64(Field.RESERVATION_ID, result.reservation_id)
            if result.reservation_id is not None
            else b"",
            _u64(Field.RETRY_AFTER_NS, result.retry_after_ns)
            if result.retry_after_ns is not None
            else b"",
            _u64(Field.ESTIMATED_START_NS, result.estimated_start_ns)
            if result.estimated_start_ns is not None
            else b"",
            _u64(Field.ESTIMATED_FINISH_NS, result.estimated_finish_ns)
            if result.estimated_finish_ns is not None
            else b"",
            _text(Field.QPM_RUNTIME_ID, result.qpm_runtime_id, MAX_SERVICE_ID)
            if result.qpm_runtime_id is not None
            else b"",
            _u64(Field.QPM_GENERATION, result.qpm_generation)
            if result.qpm_generation is not None
            else b"",
            _text(Field.DIAGNOSTIC, result.diagnostic, MAX_DIAGNOSTIC)
            if result.diagnostic is not None
            else b"",
        )
    )
    if reserve and result.decision == AdmissionDecision.ACCEPTED:
        _uint(result.reservation_id, 64, "reservation_id", nonzero=True)
    elif result.reservation_id is not None:
        raise ProtocolError("result must not contain reservation_id")
    return _tlv(Field.SERVICE_RESULT, nested)


def _encode_admission_response(
    response: ReserveResponse,
    correlation_id: int,
    message_type: MessageType,
    reserve: bool,
) -> bytes:
    _validate_admission_response(response, reserve)
    payload = _u64(Field.REQUEST_ID, response.request_id)
    payload += _u32(Field.ADMISSION_DECISION, response.decision)
    payload += b"".join(
        _encode_service_result(item, reserve) for item in response.results
    )
    return _frame(message_type, correlation_id, payload)


def encode_reserve_response(response: ReserveResponse, correlation_id: int) -> bytes:
    return _encode_admission_response(
        response, correlation_id, MessageType.RESERVE_RESPONSE, True
    )


def encode_evaluate_response(
    response: EvaluateResponse, correlation_id: int
) -> bytes:
    return _encode_admission_response(
        response, correlation_id, MessageType.EVALUATE_RESPONSE, False
    )


def _validate_admission_response(
    response: ReserveResponse, reserve: bool
) -> None:
    _uint(response.request_id, 64, "request_id", nonzero=True)
    if not 0 < len(response.results) <= MAX_SERVICES:
        raise ProtocolError("reserve response result count is invalid")
    service_ids = [item.service_id for item in response.results]
    if len(set(service_ids)) != len(service_ids):
        raise ProtocolError("reserve response contains duplicate services")
    for item in response.results:
        _string(item.service_id, MAX_SERVICE_ID, "service_id")
        try:
            AdmissionDecision(item.decision)
        except ValueError as error:
            raise ProtocolError("admission decision is invalid") from error
        _uint(item.reason_code, 64, "reason_code")
        if item.reservation_id is not None:
            _uint(item.reservation_id, 64, "reservation_id", nonzero=True)
        if not reserve and item.reservation_id is not None:
            raise ProtocolError("evaluate result contains reservation_id")
        if item.qpm_generation is not None:
            _uint(item.qpm_generation, 64, "qpm_generation")
        if item.qpm_runtime_id is not None:
            _string(item.qpm_runtime_id, MAX_SERVICE_ID, "qpm_runtime_id")
        if item.diagnostic is not None:
            _string(item.diagnostic, MAX_DIAGNOSTIC, "diagnostic")
    decisions = {item.decision for item in response.results}
    if response.decision == AdmissionDecision.ACCEPTED and decisions != {
        AdmissionDecision.ACCEPTED
    }:
        raise ProtocolError("accepted response contains non-accepted result")
    if response.decision == AdmissionDecision.REJECTED and (
        AdmissionDecision.REJECTED not in decisions
    ):
        raise ProtocolError("rejected response lacks a rejected result")
    if response.decision == AdmissionDecision.DELAYED and (
        AdmissionDecision.DELAYED not in decisions
        or AdmissionDecision.REJECTED in decisions
    ):
        raise ProtocolError("delayed response has inconsistent results")


def encode_release_request(request: ReleaseRequest, correlation_id: int) -> bytes:
    _uint(request.request_id, 64, "request_id", nonzero=True)
    _string(request.cluster_name, MAX_CLUSTER_NAME, "cluster_name")
    _uint(request.canonical_job_id, 64, "canonical_job_id", nonzero=True)
    _uint(request.reason, 32, "release_reason")
    payload = b"".join(
        (
            _u64(Field.REQUEST_ID, request.request_id),
            _text(Field.CLUSTER_NAME, request.cluster_name, MAX_CLUSTER_NAME),
            _u64(Field.CANONICAL_JOB_ID, request.canonical_job_id),
            _u32(Field.RELEASE_REASON, request.reason),
        )
    )
    return _frame(MessageType.RELEASE_REQUEST, correlation_id, payload)


def decode_release_request(frame: bytes) -> tuple[Header, ReleaseRequest]:
    header = decode_header(frame)
    if header.message_type != MessageType.RELEASE_REQUEST:
        raise ProtocolError("expected release request")
    values, repeated = _collect(frame[HEADER_SIZE:], set())
    allowed = {
        Field.REQUEST_ID,
        Field.CLUSTER_NAME,
        Field.CANONICAL_JOB_ID,
        Field.RELEASE_REASON,
    }
    if repeated or set(values) != allowed:
        raise ProtocolError("release request fields are invalid")
    request = ReleaseRequest(
        request_id=_decode_u64(values[Field.REQUEST_ID], "request_id"),
        cluster_name=_decode_text(
            values[Field.CLUSTER_NAME], MAX_CLUSTER_NAME, "cluster_name"
        ),
        canonical_job_id=_decode_u64(
            values[Field.CANONICAL_JOB_ID], "canonical_job_id"
        ),
        reason=_decode_u32(values[Field.RELEASE_REASON], "release_reason"),
    )
    _uint(request.request_id, 64, "request_id", nonzero=True)
    _uint(request.canonical_job_id, 64, "canonical_job_id", nonzero=True)
    return header, request


def encode_get_reservations_request(
    request: GetReservationsRequest, correlation_id: int
) -> bytes:
    _uint(request.request_id, 64, "request_id", nonzero=True)
    _string(request.cluster_name, MAX_CLUSTER_NAME, "cluster_name")
    _uint(request.observed_job_id, 64, "observed_job_id", nonzero=True)
    _uint(request.job_uid, 32, "job_uid")
    _uint(request.job_gid, 32, "job_gid")
    payload = b"".join(
        (
            _u64(Field.REQUEST_ID, request.request_id),
            _text(Field.CLUSTER_NAME, request.cluster_name, MAX_CLUSTER_NAME),
            _u64(Field.OBSERVED_JOB_ID, request.observed_job_id),
            _u32(Field.JOB_UID, request.job_uid),
            _u32(Field.JOB_GID, request.job_gid),
        )
    )
    return _frame(MessageType.GET_RESERVATIONS_REQUEST, correlation_id, payload)


def decode_get_reservations_request(
    frame: bytes,
) -> tuple[Header, GetReservationsRequest]:
    header = decode_header(frame)
    if header.message_type != MessageType.GET_RESERVATIONS_REQUEST:
        raise ProtocolError("expected get-reservations request")
    values, repeated = _collect(frame[HEADER_SIZE:], set())
    allowed = {
        Field.REQUEST_ID,
        Field.CLUSTER_NAME,
        Field.OBSERVED_JOB_ID,
        Field.JOB_UID,
        Field.JOB_GID,
    }
    if repeated or set(values) != allowed:
        raise ProtocolError("get-reservations request fields are invalid")
    request = GetReservationsRequest(
        request_id=_decode_u64(values[Field.REQUEST_ID], "request_id"),
        cluster_name=_decode_text(
            values[Field.CLUSTER_NAME], MAX_CLUSTER_NAME, "cluster_name"
        ),
        observed_job_id=_decode_u64(
            values[Field.OBSERVED_JOB_ID], "observed_job_id"
        ),
        job_uid=_decode_u32(values[Field.JOB_UID], "job_uid"),
        job_gid=_decode_u32(values[Field.JOB_GID], "job_gid"),
    )
    _uint(request.request_id, 64, "request_id", nonzero=True)
    _uint(request.observed_job_id, 64, "observed_job_id", nonzero=True)
    return header, request


def _encode_release_result(result: ReleaseResult) -> bytes:
    nested = b"".join(
        (
            _text(Field.SERVICE_ID, result.service_id, MAX_SERVICE_ID),
            _u64(Field.RESERVATION_ID, result.reservation_id),
            _u32(Field.RESERVATION_STATE, result.state),
            _u32(Field.GATEWAY_ERROR_CODE, result.gateway_error)
            if result.gateway_error is not None
            else b"",
            _text(Field.DIAGNOSTIC, result.diagnostic, MAX_DIAGNOSTIC)
            if result.diagnostic is not None
            else b"",
        )
    )
    return _tlv(Field.RELEASE_RESULT, nested)


def encode_release_response(response: ReleaseResponse, correlation_id: int) -> bytes:
    _uint(response.request_id, 64, "request_id", nonzero=True)
    if len(response.results) > MAX_SERVICES:
        raise ProtocolError("release response result count is invalid")
    service_ids = [item.service_id for item in response.results]
    if len(set(service_ids)) != len(service_ids):
        raise ProtocolError("release response contains duplicate services")
    for item in response.results:
        _string(item.service_id, MAX_SERVICE_ID, "service_id")
        _uint(item.reservation_id, 64, "reservation_id", nonzero=True)
        try:
            ReservationState(item.state)
        except ValueError as error:
            raise ProtocolError("reservation state is invalid") from error
        if item.gateway_error is not None:
            try:
                GatewayError(item.gateway_error)
            except ValueError as error:
                raise ProtocolError("gateway error is invalid") from error
        if item.diagnostic is not None:
            _string(item.diagnostic, MAX_DIAGNOSTIC, "diagnostic")
    payload = _u64(Field.REQUEST_ID, response.request_id)
    payload += b"".join(_encode_release_result(item) for item in response.results)
    return _frame(MessageType.RELEASE_RESPONSE, correlation_id, payload)


def _encode_reservation(reservation: Reservation) -> bytes:
    _string(reservation.service_id, MAX_SERVICE_ID, "service_id")
    _uint(reservation.reservation_id, 64, "reservation_id", nonzero=True)
    nested = _text(Field.SERVICE_ID, reservation.service_id, MAX_SERVICE_ID)
    nested += _u64(Field.RESERVATION_ID, reservation.reservation_id)
    return _tlv(Field.RESERVATION, nested)


def encode_get_reservations_response(
    response: GetReservationsResponse, correlation_id: int
) -> bytes:
    _uint(response.request_id, 64, "request_id", nonzero=True)
    _uint(response.canonical_job_id, 64, "canonical_job_id", nonzero=True)
    if not 0 < len(response.reservations) <= MAX_SERVICES:
        raise ProtocolError("reservation response count is invalid")
    services = [item.service_id for item in response.reservations]
    if len(set(services)) != len(services):
        raise ProtocolError("reservation response contains duplicate services")
    payload = _u64(Field.REQUEST_ID, response.request_id)
    payload += _u64(Field.CANONICAL_JOB_ID, response.canonical_job_id)
    payload += b"".join(
        _encode_reservation(item) for item in response.reservations
    )
    return _frame(MessageType.GET_RESERVATIONS_RESPONSE, correlation_id, payload)


def _decode_reservation(payload: bytes) -> Reservation:
    values, repeated = _collect(payload, set())
    if repeated or set(values) != {Field.SERVICE_ID, Field.RESERVATION_ID}:
        raise ProtocolError("reservation tuple fields are invalid")
    reservation = Reservation(
        service_id=_decode_text(
            values[Field.SERVICE_ID], MAX_SERVICE_ID, "service_id"
        ),
        reservation_id=_decode_u64(
            values[Field.RESERVATION_ID], "reservation_id"
        ),
    )
    _uint(reservation.reservation_id, 64, "reservation_id", nonzero=True)
    return reservation


def decode_get_reservations_response(
    frame: bytes,
) -> tuple[Header, GetReservationsResponse]:
    header = decode_header(frame)
    if header.message_type != MessageType.GET_RESERVATIONS_RESPONSE:
        raise ProtocolError("expected get-reservations response")
    values, repeated = _collect(frame[HEADER_SIZE:], {Field.RESERVATION})
    if set(values) != {Field.REQUEST_ID, Field.CANONICAL_JOB_ID}:
        raise ProtocolError("get-reservations response fields are invalid")
    reservations = tuple(
        _decode_reservation(item) for item in repeated[Field.RESERVATION]
    )
    if not 0 < len(reservations) <= MAX_SERVICES:
        raise ProtocolError("reservation response count is invalid")
    services = [item.service_id for item in reservations]
    if len(set(services)) != len(services):
        raise ProtocolError("reservation response contains duplicate services")
    response = GetReservationsResponse(
        request_id=_decode_u64(values[Field.REQUEST_ID], "request_id"),
        canonical_job_id=_decode_u64(
            values[Field.CANONICAL_JOB_ID], "canonical_job_id"
        ),
        reservations=reservations,
    )
    _uint(response.request_id, 64, "request_id", nonzero=True)
    _uint(response.canonical_job_id, 64, "canonical_job_id", nonzero=True)
    return header, response


def encode_error_response(response: ErrorResponse, correlation_id: int) -> bytes:
    try:
        GatewayError(response.error_code)
    except ValueError as error:
        raise ProtocolError("gateway error is invalid") from error
    payload = b""
    if response.request_id is not None:
        payload += _u64(Field.REQUEST_ID, response.request_id)
    payload += _u32(Field.GATEWAY_ERROR_CODE, response.error_code)
    if response.diagnostic is not None:
        payload += _text(Field.DIAGNOSTIC, response.diagnostic, MAX_DIAGNOSTIC)
    return _frame(MessageType.ERROR_RESPONSE, correlation_id, payload)


def decode_request(
    frame: bytes,
) -> tuple[
    Header,
    EvaluateRequest | ReserveRequest | ReleaseRequest | GetReservationsRequest,
]:
    header = decode_header(frame)
    if header.message_type == MessageType.EVALUATE_REQUEST:
        return decode_evaluate_request(frame)
    if header.message_type == MessageType.RESERVE_REQUEST:
        return decode_reserve_request(frame)
    if header.message_type == MessageType.RELEASE_REQUEST:
        return decode_release_request(frame)
    if header.message_type == MessageType.GET_RESERVATIONS_REQUEST:
        return decode_get_reservations_request(frame)
    raise ProtocolError("gateway accepts only request message types")


def encode_response(
    response: (
        EvaluateResponse
        | ReserveResponse
        | ReleaseResponse
        | GetReservationsResponse
        | ErrorResponse
    ),
    correlation_id: int,
) -> bytes:
    if isinstance(response, EvaluateResponse):
        return encode_evaluate_response(response, correlation_id)
    if isinstance(response, ReserveResponse):
        return encode_reserve_response(response, correlation_id)
    if isinstance(response, ReleaseResponse):
        return encode_release_response(response, correlation_id)
    if isinstance(response, GetReservationsResponse):
        return encode_get_reservations_response(response, correlation_id)
    if isinstance(response, ErrorResponse):
        return encode_error_response(response, correlation_id)
    raise TypeError("unsupported QSGP response type")


def response_to_dict(
    response: (
        EvaluateResponse
        | ReserveResponse
        | ReleaseResponse
        | GetReservationsResponse
        | ErrorResponse
    ),
) -> dict:
    value = dataclasses.asdict(response)
    value["response_type"] = type(response).__name__
    return _enum_values(value)


def _enum_values(value):
    if isinstance(value, enum.IntEnum):
        return int(value)
    if isinstance(value, dict):
        return {key: _enum_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_enum_values(item) for item in value]
    return value


def response_from_dict(
    value: dict,
) -> (
    EvaluateResponse
    | ReserveResponse
    | ReleaseResponse
    | GetReservationsResponse
    | ErrorResponse
):
    response_type = value.get("response_type")
    if response_type in {"EvaluateResponse", "ReserveResponse"}:
        response_class = (
            EvaluateResponse
            if response_type == "EvaluateResponse"
            else ReserveResponse
        )
        return response_class(
            request_id=value["request_id"],
            decision=AdmissionDecision(value["decision"]),
            results=tuple(
                ServiceResult(
                    service_id=item["service_id"],
                    decision=AdmissionDecision(item["decision"]),
                    reason_code=item["reason_code"],
                    reservation_id=item.get("reservation_id"),
                    retry_after_ns=item.get("retry_after_ns"),
                    estimated_start_ns=item.get("estimated_start_ns"),
                    estimated_finish_ns=item.get("estimated_finish_ns"),
                    qpm_runtime_id=item.get("qpm_runtime_id"),
                    qpm_generation=item.get("qpm_generation"),
                    diagnostic=item.get("diagnostic"),
                )
                for item in value["results"]
            ),
        )
    if response_type == "ReleaseResponse":
        return ReleaseResponse(
            request_id=value["request_id"],
            results=tuple(
                ReleaseResult(
                    service_id=item["service_id"],
                    reservation_id=item["reservation_id"],
                    state=ReservationState(item["state"]),
                    gateway_error=GatewayError(item["gateway_error"])
                    if item.get("gateway_error") is not None
                    else None,
                    diagnostic=item.get("diagnostic"),
                )
                for item in value["results"]
            ),
        )
    if response_type == "GetReservationsResponse":
        return GetReservationsResponse(
            request_id=value["request_id"],
            canonical_job_id=value["canonical_job_id"],
            reservations=tuple(
                Reservation(
                    service_id=item["service_id"],
                    reservation_id=item["reservation_id"],
                )
                for item in value["reservations"]
            ),
        )
    if response_type == "ErrorResponse":
        return ErrorResponse(
            error_code=GatewayError(value["error_code"]),
            request_id=value.get("request_id"),
            diagnostic=value.get("diagnostic"),
        )
    raise ProtocolError("stored response type is invalid")
