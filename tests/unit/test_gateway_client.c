#define _POSIX_C_SOURCE 200809L

#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "qfw_slurm_native.h"

#define CHECK(condition) do { \
	if (!(condition)) { \
		fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, \
			__LINE__, #condition); \
		return 1; \
	} \
} while (0)

enum server_mode {
	SERVER_ACCEPT,
	SERVER_REMOTE_ERROR,
	SERVER_WRONG_CORRELATION,
	SERVER_WRONG_TYPE,
	SERVER_TIMEOUT,
};

static int listener_create(char *port, size_t port_size)
{
	struct sockaddr_in address = {
		.sin_family = AF_INET,
		.sin_addr.s_addr = htonl(INADDR_LOOPBACK),
		.sin_port = 0,
	};
	socklen_t address_size = sizeof(address);
	int descriptor;

	descriptor = socket(AF_INET, SOCK_STREAM, 0);
	if (descriptor < 0 || bind(descriptor, (struct sockaddr *)&address,
		sizeof(address)) != 0 || listen(descriptor, 1) != 0 ||
	    getsockname(descriptor, (struct sockaddr *)&address,
		&address_size) != 0) {
		if (descriptor >= 0)
			(void)close(descriptor);
		return -1;
	}
	(void)snprintf(port, port_size, "%u", ntohs(address.sin_port));
	return descriptor;
}

static int response_frame(enum server_mode mode,
	const struct qsgp_header *request_header,
	const struct qsgp_reserve_request *request, struct qsgp_frame *frame)
{
	uint64_t correlation = request_header->correlation_id;

	if (mode == SERVER_WRONG_CORRELATION)
		correlation++;
	if (mode == SERVER_REMOTE_ERROR) {
		struct qsgp_error_response response = {
			.request_id = request->request_id,
			.error_code = QSGP_GATEWAY_ERROR_QPM,
			.has_request_id = true,
			.has_diagnostic = true,
		};

		(void)snprintf(response.diagnostic,
			sizeof(response.diagnostic), "configured gateway error");
		return qsgp_encode_error_response(&response, correlation, frame);
	}
	if (mode == SERVER_WRONG_TYPE) {
		struct qsgp_release_response response = {
			.request_id = request->request_id,
		};

		return qsgp_encode_release_response(&response, correlation, frame);
	}
	{
		struct qsgp_reserve_response response = {
			.request_id = request->request_id,
			.decision = QSGP_ADMISSION_ACCEPTED,
			.result_count = 1,
			.results = {
				{
					.service_id = "nwqsim-site",
					.decision = QSGP_ADMISSION_ACCEPTED,
					.reservation_id = 41,
					.has_reservation_id = true,
				},
			},
		};

		return qsgp_encode_reserve_response(&response, correlation, frame);
	}
}

static int server_run(int listener, enum server_mode mode)
{
	struct qsgp_peer_identity identity;
	struct qsgp_reserve_request request;
	struct qsgp_header request_header;
	struct qsgp_frame response;
	struct timespec deadline;
	uint8_t *credential = NULL;
	size_t credential_size = 0;
	uint8_t *request_frame = NULL;
	size_t request_frame_size = 0;
	uint8_t *response_credential = NULL;
	size_t response_credential_size = 0;
	int connection = -1;
	int status = 1;

	connection = accept(listener, NULL, NULL);
	if (connection < 0 || qsgp_deadline_after_ms(&deadline, 1000) !=
	    QSGP_OK)
		goto out;
	if (qsgp_receive_credential(connection, &credential,
		&credential_size, QSGP_MAX_CREDENTIAL_SIZE, &deadline) != QSGP_OK ||
	    qsgp_munge_decode(credential, credential_size, &request_frame,
		&request_frame_size, &identity) != QSGP_OK ||
	    qsgp_decode_reserve_request(request_frame, request_frame_size,
		&request_header, &request) != QSGP_OK)
		goto out;
	if (mode == SERVER_TIMEOUT) {
		struct timespec delay = {.tv_nsec = 200000000L};

		(void)nanosleep(&delay, NULL);
		status = 0;
		goto out;
	}
	if (response_frame(mode, &request_header, &request, &response) !=
	    QSGP_OK)
		goto out;
	if (qsgp_munge_encode(response.data, response.size,
		&response_credential, &response_credential_size) != QSGP_OK) {
		qsgp_frame_destroy(&response);
		goto out;
	}
	qsgp_frame_destroy(&response);
	if (qsgp_send_credential(connection, response_credential,
		response_credential_size, &deadline) != QSGP_OK)
		goto out;
	status = 0;
out:
	if (connection >= 0)
		(void)close(connection);
	(void)close(listener);
	free(credential);
	qsgp_munge_free(request_frame);
	qsgp_munge_free(response_credential);
	return status;
}

static int run_exchange(enum server_mode mode,
	struct qsgp_reserve_response *response,
	struct qfw_gateway_call_error *error)
{
	struct qfw_plugin_config config;
	struct qfw_gateway_client client;
	struct qsgp_reserve_request request = {
		.request_id = 99,
		.cluster_name = "cluster-a",
		.canonical_job_id = 123,
		.job_uid = 1001,
		.job_gid = 1001,
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
	char port[QFW_PLUGIN_MAX_PORT + 1U];
	char text[128];
	pid_t child;
	int listener;
	int child_status;
	int status;

	listener = listener_create(port, sizeof(port));
	if (listener < 0)
		return QSGP_ERR_IO;
	child = fork();
	if (child < 0) {
		(void)close(listener);
		return QSGP_ERR_IO;
	}
	if (child == 0)
		_exit(server_run(listener, mode));
	(void)close(listener);
	qfw_plugin_config_init(&config);
	(void)snprintf(config.gateway_host, sizeof(config.gateway_host),
		"127.0.0.1");
	(void)snprintf(config.gateway_port, sizeof(config.gateway_port), "%s",
		port);
	config.connect_timeout_ms = mode == SERVER_TIMEOUT ? 50 : 1000;
	config.request_timeout_ms = mode == SERVER_TIMEOUT ? 50 : 1000;
	config.expected_munge_uid = getuid();
	if (qfw_gateway_client_init(&client, &config, text, sizeof(text)) !=
	    QFW_GATEWAY_OK)
		return QSGP_ERR_INVALID;
	status = qfw_gateway_reserve(&client, &request, response, error);
	qfw_gateway_client_destroy(&client);
	if (waitpid(child, &child_status, 0) != child ||
	    !WIFEXITED(child_status) || WEXITSTATUS(child_status) != 0)
		return QSGP_ERR_IO;
	return status;
}

static int test_client_configuration(void)
{
	struct qfw_plugin_config config;
	struct qfw_gateway_client client;
	char error[128];

	qfw_plugin_config_init(&config);
	CHECK(qfw_gateway_client_init(&client, &config, error,
		sizeof(error)) == QSGP_ERR_INVALID);
	(void)snprintf(config.gateway_host, sizeof(config.gateway_host),
		"127.0.0.1");
	(void)snprintf(config.gateway_port, sizeof(config.gateway_port), "1");
	config.expected_munge_uid = getuid();
	CHECK(qfw_gateway_client_init(&client, &config, error,
		sizeof(error)) == QFW_GATEWAY_OK);
	CHECK(strcmp(client.host, "127.0.0.1") == 0);
	qfw_gateway_client_destroy(&client);
	CHECK(client.host[0] == '\0');
	return 0;
}

static int test_exchanges(void)
{
	struct qsgp_reserve_response response;
	struct qfw_gateway_call_error error;
	int status;

	status = run_exchange(SERVER_ACCEPT, &response, &error);
	CHECK(status == QFW_GATEWAY_OK);
	CHECK(response.request_id == 99);
	CHECK(response.results[0].reservation_id == 41);
	status = run_exchange(SERVER_REMOTE_ERROR, &response, &error);
	CHECK(status == QFW_GATEWAY_ERR_REMOTE);
	CHECK(error.source == QFW_GATEWAY_ERROR_REMOTE);
	CHECK(error.remote.error_code == QSGP_GATEWAY_ERROR_QPM);
	CHECK(strcmp(qfw_gateway_call_error_message(&error),
		"configured gateway error") == 0);
	status = run_exchange(SERVER_WRONG_CORRELATION, &response, &error);
	CHECK(status == QSGP_ERR_CONFLICT);
	CHECK(error.source == QFW_GATEWAY_ERROR_PROTOCOL);
	status = run_exchange(SERVER_WRONG_TYPE, &response, &error);
	CHECK(status == QSGP_ERR_INVALID);
	CHECK(error.source == QFW_GATEWAY_ERROR_PROTOCOL);
	status = run_exchange(SERVER_TIMEOUT, &response, &error);
	CHECK(status == QSGP_ERR_TIMEOUT);
	CHECK(error.source == QFW_GATEWAY_ERROR_TRANSPORT);
	return 0;
}

int main(void)
{
	CHECK(test_client_configuration() == 0);
	CHECK(test_exchanges() == 0);
	return 0;
}
