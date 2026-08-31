#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "qsgp/qsgp_protocol.h"

static const struct qsgp_reserve_request fixture = {
	.request_id = UINT64_C(0x8000000000000001),
	.cluster_name = "test-cluster",
	.canonical_job_id = 42,
	.hetero_job_id = 40,
	.hetero_component = 2,
	.has_hetero_job_id = true,
	.has_hetero_component = true,
	.job_uid = 1001,
	.job_gid = 1001,
	.workload = {
		.kind = QSGP_WORKLOAD_HYBRID,
		.walltime_ns = UINT64_C(60000000000),
		.circuit_count = 10,
		.max_qubits = 20,
		.max_depth = 100,
		.max_shots = 1024,
		.max_one_q_gates = 200,
		.max_two_q_gates = 50,
		.max_measurements = 20,
		.has_max_one_q_gates = true,
		.has_max_two_q_gates = true,
		.has_max_measurements = true,
	},
	.has_workload = true,
	.service_count = 2,
	.service_ids = {"iqm-ornl-20q", "nwqsim-site"},
};

static int encode_fixture(void)
{
	struct qsgp_frame frame;
	int status;

	status = qsgp_encode_reserve_request(&fixture, 99, &frame);
	if (status != QSGP_OK)
		return 1;
	if (fwrite(frame.data, 1, frame.size, stdout) != frame.size) {
		qsgp_frame_destroy(&frame);
		return 1;
	}
	qsgp_frame_destroy(&frame);
	return 0;
}

static int decode_fixture(const char *path)
{
	struct qsgp_reserve_request request;
	struct qsgp_header header;
	uint8_t *data;
	long file_size;
	FILE *stream;
	int status;

	stream = fopen(path, "rb");
	if (stream == NULL || fseek(stream, 0, SEEK_END) != 0)
		return 1;
	file_size = ftell(stream);
	if (file_size <= 0 || file_size > QSGP_MAX_FRAME_SIZE ||
	    fseek(stream, 0, SEEK_SET) != 0) {
		fclose(stream);
		return 1;
	}
	data = malloc((size_t)file_size);
	if (data == NULL) {
		fclose(stream);
		return 1;
	}
	if (fread(data, 1, (size_t)file_size, stream) != (size_t)file_size) {
		free(data);
		fclose(stream);
		return 1;
	}
	fclose(stream);
	status = qsgp_decode_reserve_request(data, (size_t)file_size,
		&header, &request);
	free(data);
	if (status != QSGP_OK || header.correlation_id != 99 ||
	    request.request_id != fixture.request_id ||
	    request.service_count != fixture.service_count ||
	    strcmp(request.service_ids[0], fixture.service_ids[0]) != 0 ||
	    strcmp(request.service_ids[1], fixture.service_ids[1]) != 0 ||
	    request.workload.max_shots != fixture.workload.max_shots)
		return 1;
	return 0;
}

int main(int argc, char **argv)
{
	if (argc == 2 && strcmp(argv[1], "encode") == 0)
		return encode_fixture();
	if (argc == 3 && strcmp(argv[1], "decode") == 0)
		return decode_fixture(argv[2]);
	fprintf(stderr, "usage: %s encode|decode FILE\n", argv[0]);
	return 2;
}
