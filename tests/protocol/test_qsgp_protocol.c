#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "qsgp/qsgp_protocol.h"

#define CHECK(condition) do { \
	if (!(condition)) { \
		fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, \
			__LINE__, #condition); \
		return 1; \
	} \
} while (0)

static int test_reserve_request(void)
{
	struct qsgp_reserve_request input = {
		.request_id = UINT64_C(0xfedcba9876543210),
		.cluster_name = "qfw-cluster",
		.canonical_job_id = UINT64_C(0x0102030405060708),
		.hetero_job_id = 41,
		.hetero_component = 2,
		.has_hetero_job_id = true,
		.has_hetero_component = true,
		.job_uid = 1101,
		.job_gid = 1101,
		.workload = {
			.kind = QSGP_WORKLOAD_HYBRID,
			.walltime_ns = UINT64_C(3600000000000),
			.circuit_count = 20,
			.max_qubits = 5,
			.max_depth = 120,
			.max_shots = 1024,
			.max_one_q_gates = 300,
			.max_two_q_gates = 80,
			.max_measurements = 5,
			.has_max_one_q_gates = true,
			.has_max_two_q_gates = true,
			.has_max_measurements = true,
		},
		.has_workload = true,
		.service_count = 2,
		.service_ids = {"iqm-ornl-20q", "nwqsim-site"},
	};
	struct qsgp_reserve_request output;
	struct qsgp_header header;
	struct qsgp_frame frame;
	int status;

	status = qsgp_encode_reserve_request(&input,
		UINT64_C(0x1122334455667788), &frame);
	CHECK(status == QSGP_OK);
	CHECK(frame.size > QSGP_HEADER_SIZE);
	CHECK(memcmp(frame.data, "QSGP\x00\x01\x00\x00\x00\x01", 10) == 0);
	status = qsgp_decode_reserve_request(frame.data, frame.size,
		&header, &output);
	CHECK(status == QSGP_OK);
	CHECK(header.message_type == QSGP_RESERVE_REQUEST);
	CHECK(header.correlation_id == UINT64_C(0x1122334455667788));
	CHECK(output.request_id == input.request_id);
	CHECK(output.canonical_job_id == input.canonical_job_id);
	CHECK(output.job_uid == 1101 && output.job_gid == 1101);
	CHECK(output.has_hetero_job_id && output.hetero_job_id == 41);
	CHECK(output.has_hetero_component && output.hetero_component == 2);
	CHECK(output.workload.max_shots == 1024);
	CHECK(output.has_workload);
	CHECK(output.workload.has_max_measurements);
	CHECK(output.service_count == 2);
	CHECK(strcmp(output.service_ids[0], "iqm-ornl-20q") == 0);
	CHECK(strcmp(output.service_ids[1], "nwqsim-site") == 0);
	qsgp_frame_destroy(&frame);
	return 0;
}

static int test_reserve_retrieval_request(void)
{
	struct qsgp_reserve_request input = {
		.request_id = 8,
		.cluster_name = "cluster-a",
		.canonical_job_id = 42,
		.job_uid = 1000,
		.job_gid = 1000,
		.service_count = 1,
		.service_ids = {"nwqsim-site"},
	};
	struct qsgp_reserve_request output;
	struct qsgp_header header;
	struct qsgp_frame frame;

	CHECK(qsgp_encode_reserve_request(&input, 9, &frame) == QSGP_OK);
	CHECK(qsgp_decode_reserve_request(frame.data, frame.size, &header,
		&output) == QSGP_OK);
	CHECK(!output.has_workload);
	CHECK(output.service_count == 1);
	qsgp_frame_destroy(&frame);
	return 0;
}

static int test_duplicate_service_request(void)
{
	struct qsgp_reserve_request input = {
		.request_id = 8,
		.cluster_name = "cluster-a",
		.canonical_job_id = 42,
		.job_uid = 1000,
		.job_gid = 1000,
		.service_count = 2,
		.service_ids = {"nwqsim-site", "nwqsim-site"},
	};
	struct qsgp_frame frame;

	CHECK(qsgp_encode_reserve_request(&input, 9, &frame) ==
		QSGP_ERR_CONFLICT);
	return 0;
}

static int test_evaluate_messages(void)
{
	struct qsgp_reserve_request request = {
		.request_id = 81,
		.cluster_name = "cluster-a",
		.canonical_job_id = 42,
		.job_uid = 1000,
		.job_gid = 1000,
		.has_workload = true,
		.workload = {
			.kind = QSGP_WORKLOAD_QUANTUM,
			.walltime_ns = 1000000,
			.circuit_count = 1,
			.max_qubits = 2,
			.max_depth = 3,
			.max_shots = 4,
		},
		.service_count = 1,
		.service_ids = {"nwqsim-site"},
	};
	struct qsgp_reserve_request decoded_request;
	struct qsgp_reserve_response response = {
		.request_id = 81,
		.decision = QSGP_ADMISSION_ACCEPTED,
		.result_count = 1,
		.results = {{
			.service_id = "nwqsim-site",
			.decision = QSGP_ADMISSION_ACCEPTED,
		}},
	};
	struct qsgp_reserve_response decoded_response;
	struct qsgp_header header;
	struct qsgp_frame frame;

	CHECK(qsgp_encode_evaluate_request(&request, 82, &frame) == QSGP_OK);
	CHECK(qsgp_decode_evaluate_request(frame.data, frame.size, &header,
		&decoded_request) == QSGP_OK);
	CHECK(header.message_type == QSGP_EVALUATE_REQUEST);
	CHECK(decoded_request.request_id == request.request_id);
	qsgp_frame_destroy(&frame);

	CHECK(qsgp_encode_evaluate_response(&response, 83, &frame) == QSGP_OK);
	CHECK(qsgp_decode_evaluate_response(frame.data, frame.size, &header,
		&decoded_response) == QSGP_OK);
	CHECK(header.message_type == QSGP_EVALUATE_RESPONSE);
	CHECK(!decoded_response.results[0].has_reservation_id);
	qsgp_frame_destroy(&frame);

	response.results[0].reservation_id = 9;
	response.results[0].has_reservation_id = true;
	CHECK(qsgp_encode_evaluate_response(&response, 84, &frame) ==
		QSGP_ERR_INVALID);
	return 0;
}

static int test_reserve_response(void)
{
	struct qsgp_reserve_response input = {
		.request_id = 91,
		.decision = QSGP_ADMISSION_ACCEPTED,
		.result_count = 2,
		.results = {
			{
				.service_id = "iqm-ornl-20q",
				.decision = QSGP_ADMISSION_ACCEPTED,
				.reservation_id = 41,
				.reason_code = 0,
				.qpm_runtime_id = "runtime-a",
				.qpm_generation = 3,
				.has_reservation_id = true,
				.has_runtime_id = true,
				.has_generation = true,
			},
			{
				.service_id = "nwqsim-site",
				.decision = QSGP_ADMISSION_ACCEPTED,
				.reservation_id = UINT64_MAX,
				.reason_code = 0,
				.has_reservation_id = true,
			},
		},
	};
	struct qsgp_reserve_response output;
	struct qsgp_header header;
	struct qsgp_frame frame;

	CHECK(qsgp_encode_reserve_response(&input, 100, &frame) == QSGP_OK);
	CHECK(qsgp_decode_reserve_response(frame.data, frame.size,
		&header, &output) == QSGP_OK);
	CHECK(output.request_id == 91);
	CHECK(output.decision == QSGP_ADMISSION_ACCEPTED);
	CHECK(output.result_count == 2);
	CHECK(output.results[0].has_generation);
	CHECK(output.results[0].qpm_generation == 3);
	CHECK(output.results[1].reservation_id == UINT64_MAX);
	qsgp_frame_destroy(&frame);
	return 0;
}

static int test_nonaccepted_response(void)
{
	struct qsgp_reserve_response input = {
		.request_id = 92,
		.decision = QSGP_ADMISSION_REJECTED,
		.result_count = 1,
		.results = {{
			.service_id = "iqm-ornl-20q",
			.decision = QSGP_ADMISSION_REJECTED,
			.reason_code = 27,
			.diagnostic = "user is not entitled",
			.has_diagnostic = true,
		}},
	};
	struct qsgp_reserve_response output;
	struct qsgp_header header;
	struct qsgp_frame frame;

	CHECK(qsgp_encode_reserve_response(&input, 101, &frame) == QSGP_OK);
	CHECK(qsgp_decode_reserve_response(frame.data, frame.size,
		&header, &output) == QSGP_OK);
	CHECK(!output.results[0].has_reservation_id);
	CHECK(output.results[0].reason_code == 27);
	CHECK(strcmp(output.results[0].diagnostic,
		"user is not entitled") == 0);
	qsgp_frame_destroy(&frame);
	return 0;
}

static int test_release_messages(void)
{
	struct qsgp_release_request request = {
		.request_id = 201,
		.cluster_name = "qfw-cluster",
		.canonical_job_id = 1234,
		.reason = 9,
	};
	struct qsgp_release_request decoded_request;
	struct qsgp_release_response response = {
		.request_id = 201,
		.result_count = 1,
		.results = {{
			.service_id = "nwqsim-site",
			.reservation_id = 17,
			.state = QSGP_RESERVATION_RELEASED,
		}},
	};
	struct qsgp_release_response decoded_response;
	struct qsgp_header header;
	struct qsgp_frame frame;

	CHECK(qsgp_encode_release_request(&request, 202, &frame) == QSGP_OK);
	CHECK(qsgp_decode_release_request(frame.data, frame.size,
		&header, &decoded_request) == QSGP_OK);
	CHECK(decoded_request.reason == 9);
	qsgp_frame_destroy(&frame);
	CHECK(qsgp_encode_release_response(&response, 203, &frame) == QSGP_OK);
	CHECK(qsgp_decode_release_response(frame.data, frame.size,
		&header, &decoded_response) == QSGP_OK);
	CHECK(decoded_response.result_count == 1);
	CHECK(decoded_response.results[0].reservation_id == 17);
	qsgp_frame_destroy(&frame);
	return 0;
}

static int test_error_response(void)
{
	struct qsgp_error_response response = {
		.request_id = 301,
		.error_code = QSGP_GATEWAY_ERROR_REQUEST_CONFLICT,
		.diagnostic = "request fingerprint changed",
		.has_request_id = true,
		.has_diagnostic = true,
	};
	struct qsgp_error_response decoded;
	struct qsgp_header header;
	struct qsgp_frame frame;

	CHECK(qsgp_encode_error_response(&response, 302, &frame) == QSGP_OK);
	CHECK(qsgp_decode_error_response(frame.data, frame.size,
		&header, &decoded) == QSGP_OK);
	CHECK(decoded.has_request_id && decoded.request_id == 301);
	CHECK(decoded.error_code == QSGP_GATEWAY_ERROR_REQUEST_CONFLICT);
	qsgp_frame_destroy(&frame);
	return 0;
}

static int test_malformed_frames(void)
{
	struct qsgp_release_request request = {
		.request_id = 401,
		.cluster_name = "qfw-cluster",
		.canonical_job_id = 1234,
		.reason = 0,
	};
	struct qsgp_release_request decoded;
	struct qsgp_header header;
	struct qsgp_frame frame;
	size_t cluster_padding;

	CHECK(qsgp_encode_release_request(&request, 402, &frame) == QSGP_OK);
	frame.data[28] = 1;
	CHECK(qsgp_decode_release_request(frame.data, frame.size,
		&header, &decoded) == QSGP_ERR_INVALID);
	frame.data[28] = 0;
	cluster_padding = QSGP_HEADER_SIZE + 16U + 8U +
		strlen(request.cluster_name);
	frame.data[cluster_padding] = 1;
	CHECK(qsgp_decode_release_request(frame.data, frame.size,
		&header, &decoded) == QSGP_ERR_INVALID);
	frame.data[cluster_padding] = 0;
	CHECK(qsgp_decode_release_request(frame.data, frame.size - 1U,
		&header, &decoded) == QSGP_ERR_BOUNDS);
	qsgp_frame_destroy(&frame);
	return 0;
}

static int test_credential_framing(void)
{
	const uint8_t input[] = "test-credential";
	struct timespec deadline;
	uint8_t *output = NULL;
	size_t output_size = 0;
	int sockets[2];

	CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
	CHECK(qsgp_deadline_after_ms(&deadline, 1000) == QSGP_OK);
	CHECK(qsgp_send_credential(sockets[0], input, sizeof(input) - 1U,
		&deadline) == QSGP_OK);
	CHECK(qsgp_receive_credential(sockets[1], &output, &output_size,
		QSGP_MAX_CREDENTIAL_SIZE, &deadline) == QSGP_OK);
	CHECK(output_size == sizeof(input) - 1U);
	CHECK(memcmp(output, input, output_size) == 0);
	free(output);
	close(sockets[0]);
	close(sockets[1]);
	return 0;
}

int main(void)
{
	CHECK(test_reserve_request() == 0);
	CHECK(test_reserve_retrieval_request() == 0);
	CHECK(test_duplicate_service_request() == 0);
	CHECK(test_evaluate_messages() == 0);
	CHECK(test_reserve_response() == 0);
	CHECK(test_nonaccepted_response() == 0);
	CHECK(test_release_messages() == 0);
	CHECK(test_error_response() == 0);
	CHECK(test_malformed_frames() == 0);
	CHECK(test_credential_framing() == 0);
	return 0;
}
