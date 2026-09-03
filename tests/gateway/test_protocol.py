from __future__ import annotations

import os
import subprocess
import struct

import pytest

from qfw_slurm_gateway.protocol import (
    HEADER_SIZE,
    AdmissionDecision,
    ErrorResponse,
    EvaluateRequest,
    EvaluateResponse,
    Field,
    GatewayError,
    GetReservationsRequest,
    GetReservationsResponse,
    ProtocolError,
    ReleaseRequest,
    Reservation,
    ReserveRequest,
    ReserveResponse,
    ServiceResult,
    Workload,
    WorkloadKind,
    bounded_diagnostic,
    decode_request,
    decode_get_reservations_response,
    encode_evaluate_request,
    encode_evaluate_response,
    encode_error_response,
    encode_get_reservations_request,
    encode_get_reservations_response,
    encode_release_request,
    encode_reserve_request,
    encode_reserve_response,
)


def reserve_request() -> ReserveRequest:
    return ReserveRequest(
        request_id=(1 << 63) + 1,
        cluster_name="test-cluster",
        canonical_job_id=42,
        job_uid=1001,
        job_gid=1001,
        workload=Workload(
            WorkloadKind.HYBRID,
            walltime_ns=60_000_000_000,
            circuit_count=10,
            max_qubits=20,
            max_depth=100,
            max_shots=1024,
            max_one_q_gates=200,
            max_two_q_gates=50,
            max_measurements=20,
        ),
        service_ids=("iqm-ornl-20q", "nwqsim-site"),
        hetero_job_id=40,
        hetero_component=2,
    )


def test_reserve_request_round_trip() -> None:
    request = reserve_request()
    header, decoded = decode_request(encode_reserve_request(request, 99))
    assert header.correlation_id == 99
    assert decoded == request


def test_evaluate_request_round_trip() -> None:
    request = EvaluateRequest(**reserve_request().__dict__)
    header, decoded = decode_request(encode_evaluate_request(request, 98))
    assert header.correlation_id == 98
    assert decoded == request
    assert isinstance(decoded, EvaluateRequest)


def test_evaluate_requires_workload() -> None:
    request = EvaluateRequest(
        request_id=1,
        cluster_name="test-cluster",
        canonical_job_id=42,
        job_uid=1001,
        job_gid=1001,
        workload=None,
        service_ids=("nwqsim-site",),
    )
    with pytest.raises(ProtocolError, match="requires a workload"):
        decode_request(encode_evaluate_request(request, 98))


def test_release_request_round_trip() -> None:
    request = ReleaseRequest(5, "test-cluster", 42, 3)
    header, decoded = decode_request(encode_release_request(request, 100))
    assert header.correlation_id == 100
    assert decoded == request


def test_get_reservations_round_trip() -> None:
    request = GetReservationsRequest(6, "test-cluster", 43, 1001, 1001)
    header, decoded = decode_request(
        encode_get_reservations_request(request, 101)
    )
    assert header.correlation_id == 101
    assert decoded == request

    response = GetReservationsResponse(
        request_id=6,
        canonical_job_id=42,
        reservations=(
            Reservation("iqm-ornl-20q", 41),
            Reservation("nwqsim-site", (1 << 64) - 1),
        ),
    )
    frame = encode_get_reservations_response(response, 102)
    response_header, decoded_response = decode_get_reservations_response(frame)
    assert response_header.correlation_id == 102
    assert decoded_response == response


def test_reserve_retrieval_request_round_trip() -> None:
    request = reserve_request()
    request = ReserveRequest(
        request.request_id,
        request.cluster_name,
        request.canonical_job_id,
        request.job_uid,
        request.job_gid,
        None,
        request.service_ids,
    )
    _header, decoded = decode_request(encode_reserve_request(request, 100))
    assert decoded == request


def test_unknown_required_field_is_rejected() -> None:
    frame = bytearray(encode_reserve_request(reserve_request(), 99))
    payload_size = struct.unpack_from("!I", frame, 24)[0]
    frame.extend(struct.pack("!HHI", 0x7FFE, 1, 0))
    struct.pack_into("!I", frame, 24, payload_size + 8)
    with pytest.raises(ProtocolError, match="unknown required"):
        decode_request(bytes(frame))


def test_duplicate_service_is_rejected() -> None:
    request = reserve_request()
    duplicate = ReserveRequest(
        **{**request.__dict__, "service_ids": ("nwqsim", "nwqsim")}
    )
    with pytest.raises(ProtocolError, match="service set"):
        encode_reserve_request(duplicate, 9)


def test_inconsistent_response_is_rejected() -> None:
    response = ReserveResponse(
        1,
        AdmissionDecision.ACCEPTED,
        (ServiceResult("svc", AdmissionDecision.REJECTED, 5),),
    )
    with pytest.raises(ProtocolError, match="non-accepted"):
        encode_reserve_response(response, 1)


def test_evaluate_response_rejects_reservation_id() -> None:
    response = EvaluateResponse(
        1,
        AdmissionDecision.ACCEPTED,
        (
            ServiceResult(
                "svc",
                AdmissionDecision.ACCEPTED,
                0,
                reservation_id=41,
            ),
        ),
    )
    with pytest.raises(ProtocolError, match="reservation_id"):
        encode_evaluate_response(response, 1)


def test_evaluate_response_accepts_without_reservation_id() -> None:
    response = EvaluateResponse(
        1,
        AdmissionDecision.ACCEPTED,
        (ServiceResult("svc", AdmissionDecision.ACCEPTED, 0),),
    )
    frame = encode_evaluate_response(response, 1)
    assert struct.unpack_from("!H", frame, 8)[0] == 0x8003


def test_error_response_is_bounded() -> None:
    frame = encode_error_response(
        ErrorResponse(GatewayError.INVALID_REQUEST, 1, "invalid"), 2
    )
    assert len(frame) > HEADER_SIZE
    assert struct.pack("!H", Field.GATEWAY_ERROR_CODE) in frame


def test_diagnostic_is_truncated_on_utf8_boundary() -> None:
    diagnostic = bounded_diagnostic("é" * 5000)
    assert len(diagnostic.encode("utf-8")) <= 4096
    encode_error_response(
        ErrorResponse(GatewayError.INTERNAL, 1, diagnostic), 2
    )


def test_native_python_interoperability(tmp_path) -> None:
    executable = os.environ.get("QSGP_INTEROP")
    if not executable:
        pytest.skip("native interoperability fixture was not supplied")
    native_frame = subprocess.run(
        [executable, "encode"], check=True, capture_output=True
    ).stdout
    _header, decoded = decode_request(native_frame)
    assert decoded == reserve_request()

    python_frame = tmp_path / "python.qsgp"
    python_frame.write_bytes(encode_reserve_request(reserve_request(), 99))
    subprocess.run([executable, "decode", str(python_frame)], check=True)

    get_frame = subprocess.run(
        [executable, "encode-get"], check=True, capture_output=True
    ).stdout
    _header, decoded_get = decode_request(get_frame)
    assert decoded_get == GetReservationsRequest(
        (1 << 63) + 2, "test-cluster", 43, 1001, 1001
    )

    python_get_frame = tmp_path / "python-get.qsgp"
    python_get_frame.write_bytes(
        encode_get_reservations_request(decoded_get, 100)
    )
    subprocess.run(
        [executable, "decode-get", str(python_get_frame)], check=True
    )
