#define _POSIX_C_SOURCE 200809L

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "qfw_slurm_native.h"

#define CHECK(condition) do { \
	if (!(condition)) { \
		fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, \
			__LINE__, #condition); \
		return 1; \
	} \
} while (0)

static int write_config(char *path, size_t path_size)
{
	char template[] = "/tmp/qfw-plugin-config-XXXXXX";
	FILE *stream;
	int descriptor = mkstemp(template);

	if (descriptor < 0)
		return -1;
	stream = fdopen(descriptor, "w");
	if (stream == NULL) {
		close(descriptor);
		unlink(template);
		return -1;
	}
	(void)fprintf(stream,
		"[gateway]\n"
		"host=127.0.0.1\n"
		"port=18095\n"
		"connect_timeout_ms=1000\n"
		"request_timeout_ms=5000\n"
		"max_credential_bytes=65536\n"
		"expected_munge_uid=%lu\n"
		"\n"
		"[resource \"nwqsim\"]\n"
		"service_id=nwqsim-site\n"
		"\n"
		"[resource \"ornl-iqm-20q\"]\n"
		"service_id=iqm-ornl-20q\n",
		(unsigned long)getuid());
	if (fclose(stream) != 0) {
		unlink(template);
		return -1;
	}
	if (strlen(template) >= path_size) {
		unlink(template);
		return -1;
	}
	(void)snprintf(path, path_size, "%s", template);
	return 0;
}

static int test_config(void)
{
	struct qfw_plugin_config config;
	char path[128];
	char error[256] = {0};

	CHECK(write_config(path, sizeof(path)) == 0);
	if (geteuid() != 0) {
		CHECK(qfw_plugin_config_load(path, &config, error,
			sizeof(error)) != 0);
		CHECK(unlink(path) == 0);
		return 0;
	}
	CHECK(qfw_plugin_config_load(path, &config, error, sizeof(error)) == 0);
	CHECK(strcmp(config.gateway_host, "127.0.0.1") == 0);
	CHECK(strcmp(config.gateway_port, "18095") == 0);
	CHECK(config.expected_munge_uid == getuid());
	CHECK(config.resource_count == 2);
	CHECK(strcmp(qfw_plugin_config_service_id(&config, "nwqsim"),
		"nwqsim-site") == 0);
	CHECK(qfw_plugin_config_service_id(&config, "missing") == NULL);
	CHECK(unlink(path) == 0);
	return 0;
}

static int set_required_options(struct qfw_quantum_options *options)
{
	char error[256];

	qfw_quantum_options_init(options);
	CHECK(qfw_quantum_options_set(options, QFW_OPTION_QPU,
		"ornl-iqm-20q,nwqsim", error, sizeof(error)) == 0);
	CHECK(qfw_quantum_options_set(options, QFW_OPTION_WORKLOAD_KIND,
		"hybrid", error, sizeof(error)) == 0);
	CHECK(qfw_quantum_options_set(options, QFW_OPTION_CIRCUIT_COUNT,
		"20", error, sizeof(error)) == 0);
	CHECK(qfw_quantum_options_set(options, QFW_OPTION_MAX_QUBITS,
		"5", error, sizeof(error)) == 0);
	CHECK(qfw_quantum_options_set(options, QFW_OPTION_MAX_DEPTH,
		"120", error, sizeof(error)) == 0);
	CHECK(qfw_quantum_options_set(options, QFW_OPTION_MAX_SHOTS,
		"1024", error, sizeof(error)) == 0);
	CHECK(qfw_quantum_options_set(options, QFW_OPTION_MAX_ONE_Q_GATES,
		"0", error, sizeof(error)) == 0);
	CHECK(qfw_quantum_options_validate(options, error, sizeof(error)) == 0);
	return 0;
}

static int test_options(void)
{
	struct qfw_quantum_options options;
	char error[256];

	CHECK(set_required_options(&options) == 0);
	CHECK(options.qpu_count == 2);
	CHECK(options.workload.kind == QSGP_WORKLOAD_HYBRID);
	CHECK(options.workload.has_max_one_q_gates);
	CHECK(qfw_quantum_options_set(&options, QFW_OPTION_MAX_SHOTS,
		"1024", error, sizeof(error)) == 0);
	CHECK(qfw_quantum_options_set(&options, QFW_OPTION_MAX_SHOTS,
		"2048", error, sizeof(error)) != 0);
	qfw_quantum_options_init(&options);
	CHECK(qfw_quantum_options_set(&options, QFW_OPTION_QPU,
		"nwqsim,nwqsim", error, sizeof(error)) != 0);
	CHECK(qfw_quantum_options_set(&options, QFW_OPTION_MAX_DEPTH,
		"-1", error, sizeof(error)) != 0);
	CHECK(qfw_quantum_options_set(&options, QFW_OPTION_MAX_DEPTH,
		"18446744073709551616", error, sizeof(error)) != 0);
	qfw_quantum_options_init(&options);
	CHECK(qfw_quantum_options_set(&options, QFW_OPTION_MAX_QUBITS,
		"5", error, sizeof(error)) == 0);
	CHECK(qfw_quantum_options_validate(&options, error,
		sizeof(error)) != 0);
	qfw_quantum_options_init(&options);
	CHECK(qfw_quantum_options_set(&options, QFW_OPTION_QPU,
		"nwqsim", error, sizeof(error)) == 0);
	CHECK(qfw_quantum_options_is_retrieval(&options));
	CHECK(qfw_quantum_options_validate(&options, error,
		sizeof(error)) == 0);
	return 0;
}

static int test_request_and_environment(void)
{
	struct qfw_plugin_config config;
	struct qfw_quantum_options options;
	struct qsgp_reserve_request request;
	struct qsgp_reserve_response response = {
		.request_id = 1,
		.decision = QSGP_ADMISSION_ACCEPTED,
		.result_count = 2,
		.results = {
			{
				.service_id = "nwqsim-site",
				.decision = QSGP_ADMISSION_ACCEPTED,
				.reservation_id = UINT64_MAX,
				.has_reservation_id = true,
			},
			{
				.service_id = "iqm-ornl-20q",
				.decision = QSGP_ADMISSION_ACCEPTED,
				.reservation_id = 41,
				.has_reservation_id = true,
			},
		},
	};
	char error[256] = {0};
	char output[1024];
	struct qsgp_get_reservations_response lookup = {
		.request_id = 2,
		.canonical_job_id = 100,
		.reservation_count = 2,
		.reservations = {
			{.service_id = "nwqsim-site", .reservation_id = UINT64_MAX},
			{.service_id = "iqm-ornl-20q", .reservation_id = 41},
		},
	};

	qfw_plugin_config_init(&config);
	config.resource_count = 2;
	(void)snprintf(config.resources[0].name,
		sizeof(config.resources[0].name), "nwqsim");
	(void)snprintf(config.resources[0].service_id,
		sizeof(config.resources[0].service_id), "nwqsim-site");
	(void)snprintf(config.resources[1].name,
		sizeof(config.resources[1].name), "ornl-iqm-20q");
	(void)snprintf(config.resources[1].service_id,
		sizeof(config.resources[1].service_id), "iqm-ornl-20q");
	CHECK(set_required_options(&options) == 0);
	CHECK(qfw_build_reserve_request(&config, &options, "cluster-a", 100,
		50, 1101, 1101, true, 101, 1, UINT64_C(60000000000),
		&request, error, sizeof(error)) == 0);
	CHECK(request.request_id != 0);
	CHECK(request.service_count == 2);
	CHECK(strcmp(request.service_ids[0], "iqm-ornl-20q") == 0);
	CHECK(strcmp(request.service_ids[1], "nwqsim-site") == 0);
	CHECK(request.workload.walltime_ns == UINT64_C(60000000000));
	qfw_quantum_options_init(&options);
	CHECK(qfw_quantum_options_set(&options, QFW_OPTION_QPU,
		"nwqsim", error, sizeof(error)) == 0);
	CHECK(qfw_build_reserve_request(&config, &options, "cluster-a", 100,
		50, 1101, 1101, false, 0, 0, UINT64_C(60000000000),
		&request, error, sizeof(error)) == 0);
	CHECK(!request.has_workload);
	CHECK(request.service_count == 1);
	CHECK(qfw_reservations_json(&response, output, sizeof(output)) == 0);
	CHECK(strcmp(output,
		"[[\"iqm-ornl-20q\",\"41\"],"
		"[\"nwqsim-site\",\"18446744073709551615\"]]") == 0);
	CHECK(qfw_lookup_reservations_json(&lookup, output,
		sizeof(output)) == 0);
	CHECK(strcmp(output,
		"[[\"iqm-ornl-20q\",\"41\"],"
		"[\"nwqsim-site\",\"18446744073709551615\"]]") == 0);
	lookup.reservations[1].service_id[0] = '\0';
	CHECK(qfw_lookup_reservations_json(&lookup, output,
		sizeof(output)) != 0);
	return 0;
}

static void accepted_result(struct qsgp_service_result *result,
	const char *service_id, uint64_t reservation_id)
{
	memset(result, 0, sizeof(*result));
	(void)snprintf(result->service_id, sizeof(result->service_id), "%s",
		service_id);
	result->decision = QSGP_ADMISSION_ACCEPTED;
	result->reservation_id = reservation_id;
	result->has_reservation_id = true;
}

static int test_reserve_response_processing(void)
{
	struct qsgp_reserve_request request = {
		.request_id = 91,
		.service_count = 2,
		.service_ids = {"iqm-site", "nwqsim-site"},
	};
	struct qsgp_reserve_response response = {
		.request_id = 91,
		.decision = QSGP_ADMISSION_ACCEPTED,
		.result_count = 2,
	};
	struct qfw_reserve_operation_result result = {0};

	accepted_result(&response.results[0], "nwqsim-site", UINT64_MAX);
	accepted_result(&response.results[1], "iqm-site", 41);
	CHECK(qfw_reserve_response_process(&request, &response, &result) == 0);
	CHECK(result.state == QFW_OPERATION_ACCEPTED);
	CHECK(strcmp(result.reservations_json,
		"[[\"iqm-site\",\"41\"],"
		"[\"nwqsim-site\",\"18446744073709551615\"]]") == 0);

	memset(&result, 0, sizeof(result));
	response.result_count = 1;
	CHECK(qfw_reserve_response_process(&request, &response, &result) == 0);
	CHECK(result.state == QFW_OPERATION_RESPONSE_ERROR);

	memset(&result, 0, sizeof(result));
	response.result_count = 2;
	accepted_result(&response.results[1], "nwqsim-site", 42);
	CHECK(qfw_reserve_response_process(&request, &response, &result) == 0);
	CHECK(result.state == QFW_OPERATION_RESPONSE_ERROR);

	memset(&result, 0, sizeof(result));
	accepted_result(&response.results[1], "iqm-site", 0);
	CHECK(qfw_reserve_response_process(&request, &response, &result) == 0);
	CHECK(result.state == QFW_OPERATION_RESPONSE_ERROR);

	memset(&result, 0, sizeof(result));
	response.request_id = 92;
	accepted_result(&response.results[1], "iqm-site", 42);
	CHECK(qfw_reserve_response_process(&request, &response, &result) == 0);
	CHECK(result.state == QFW_OPERATION_RESPONSE_ERROR);

	memset(&result, 0, sizeof(result));
	memset(&response, 0, sizeof(response));
	response.request_id = request.request_id;
	response.decision = QSGP_ADMISSION_DELAYED;
	response.result_count = 1;
	(void)snprintf(response.results[0].service_id,
		sizeof(response.results[0].service_id), "iqm-site");
	response.results[0].decision = QSGP_ADMISSION_DELAYED;
	response.results[0].has_diagnostic = true;
	(void)snprintf(response.results[0].diagnostic,
		sizeof(response.results[0].diagnostic), "capacity pending");
	CHECK(qfw_reserve_response_process(&request, &response, &result) == 0);
	CHECK(result.state == QFW_OPERATION_DELAYED);
	CHECK(strcmp(result.diagnostic, "capacity pending") == 0);

	memset(&result, 0, sizeof(result));
	response.decision = QSGP_ADMISSION_REJECTED;
	response.results[0].decision = QSGP_ADMISSION_REJECTED;
	response.results[0].has_diagnostic = false;
	CHECK(qfw_reserve_response_process(&request, &response, &result) == 0);
	CHECK(result.state == QFW_OPERATION_REJECTED);
	return 0;
}

static int test_release_response_processing(void)
{
	struct qsgp_release_request request = {.request_id = 72};
	struct qsgp_release_response response = {
		.request_id = 72,
		.result_count = 3,
		.results = {
			{
				.service_id = "service-a",
				.reservation_id = 1,
				.state = QSGP_RESERVATION_RELEASED,
			},
			{
				.service_id = "service-b",
				.reservation_id = 2,
				.state = QSGP_RESERVATION_QPM_FAILURE,
				.diagnostic = "QPM unavailable",
				.has_diagnostic = true,
			},
			{
				.service_id = "service-c",
				.reservation_id = 3,
				.state = QSGP_RESERVATION_GATEWAY_FAILURE,
			},
		},
	};
	struct qfw_release_operation_result result = {0};

	CHECK(qfw_release_response_process(&request, &response, &result) == 0);
	CHECK(result.state == QFW_OPERATION_RELEASE_UNRESOLVED);
	CHECK(result.unresolved_count == 2);
	CHECK(strcmp(result.diagnostic, "QPM unavailable") == 0);

	memset(&result, 0, sizeof(result));
	response.results[1].state = QSGP_RESERVATION_NOT_FOUND;
	response.results[2].state = QSGP_RESERVATION_STALE_RUNTIME;
	CHECK(qfw_release_response_process(&request, &response, &result) == 0);
	CHECK(result.state == QFW_OPERATION_RELEASED);
	CHECK(result.unresolved_count == 0);

	memset(&result, 0, sizeof(result));
	response.results[2].state = 999;
	CHECK(qfw_release_response_process(&request, &response, &result) == 0);
	CHECK(result.state == QFW_OPERATION_RESPONSE_ERROR);

	memset(&result, 0, sizeof(result));
	response.results[2].state = QSGP_RESERVATION_RELEASED;
	response.request_id = 73;
	CHECK(qfw_release_response_process(&request, &response, &result) == 0);
	CHECK(result.state == QFW_OPERATION_RESPONSE_ERROR);
	return 0;
}

int main(void)
{
	CHECK(test_config() == 0);
	CHECK(test_options() == 0);
	CHECK(test_request_and_environment() == 0);
	CHECK(test_reserve_response_processing() == 0);
	CHECK(test_release_response_processing() == 0);
	return 0;
}
