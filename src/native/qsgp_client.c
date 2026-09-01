#define _POSIX_C_SOURCE 200809L

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "qfw_slurm_native.h"

static void set_text_error(char *error, size_t error_size,
	const char *message)
{
	if (error != NULL && error_size != 0)
		(void)snprintf(error, error_size, "%s", message);
}

int qfw_gateway_client_init(struct qfw_gateway_client *client,
	const struct qfw_plugin_config *config, char *error, size_t error_size)
{
	if (client == NULL || config == NULL) {
		set_text_error(error, error_size,
			"gateway client configuration is required");
		return QSGP_ERR_INVALID;
	}
	memset(client, 0, sizeof(*client));
	if (config->gateway_host[0] == '\0' ||
	    config->gateway_port[0] == '\0' ||
	    config->connect_timeout_ms == 0 ||
	    config->request_timeout_ms < config->connect_timeout_ms ||
	    config->max_credential_bytes == 0 ||
	    config->max_credential_bytes > QSGP_MAX_CREDENTIAL_SIZE ||
	    config->expected_munge_uid == (uid_t)-1) {
		set_text_error(error, error_size,
			"gateway client configuration is invalid");
		return QSGP_ERR_INVALID;
	}
	(void)snprintf(client->host, sizeof(client->host), "%s",
		config->gateway_host);
	(void)snprintf(client->port, sizeof(client->port), "%s",
		config->gateway_port);
	client->connect_timeout_ms = config->connect_timeout_ms;
	client->request_timeout_ms = config->request_timeout_ms;
	client->max_credential_bytes = config->max_credential_bytes;
	client->expected_munge_uid = config->expected_munge_uid;
	return QFW_GATEWAY_OK;
}

void qfw_gateway_client_destroy(struct qfw_gateway_client *client)
{
	if (client != NULL)
		memset(client, 0, sizeof(*client));
}

const char *qfw_gateway_call_error_message(
	const struct qfw_gateway_call_error *error)
{
	if (error == NULL)
		return "gateway call failed";
	if (error->source == QFW_GATEWAY_ERROR_REMOTE) {
		if (error->remote.has_diagnostic)
			return error->remote.diagnostic;
		return "gateway rejected the request";
	}
	return qsgp_status_string(error->qsgp_status);
}

static int fail(struct qfw_gateway_call_error *error, uint32_t source,
	int status)
{
	if (error != NULL) {
		error->source = source;
		error->qsgp_status = status;
	}
	return status;
}

static uint64_t correlation_id(uint64_t request_id)
{
	struct timespec now;
	uint64_t value = request_id ^ (uint64_t)getpid();

	if (clock_gettime(CLOCK_MONOTONIC, &now) == 0) {
		value ^= (uint64_t)now.tv_sec;
		value ^= (uint64_t)now.tv_nsec << 32U;
	}
	return value == 0 ? UINT64_MAX : value;
}

static int gateway_exchange(const struct qfw_gateway_client *client,
	const struct qsgp_frame *request_frame, uint64_t correlation,
	uint16_t expected_response, void *response,
	struct qfw_gateway_call_error *error)
{
	struct qsgp_peer_identity identity;
	struct timespec deadline;
	struct timespec connect_deadline;
	struct qsgp_header header;
	uint8_t *request_credential = NULL;
	size_t request_credential_size = 0;
	uint8_t *response_credential = NULL;
	size_t response_credential_size = 0;
	uint8_t *response_frame = NULL;
	size_t response_frame_size = 0;
	uint32_t source = QFW_GATEWAY_ERROR_LOCAL;
	int socket_fd = -1;
	int status;

	if (client == NULL || request_frame == NULL || response == NULL ||
	    error == NULL)
		return QSGP_ERR_INVALID;
	memset(error, 0, sizeof(*error));
	status = qsgp_deadline_after_ms(&deadline, client->request_timeout_ms);
	if (status != QSGP_OK)
		goto out;
	status = qsgp_deadline_after_ms(&connect_deadline,
		client->connect_timeout_ms);
	if (status != QSGP_OK)
		goto out;
	if (connect_deadline.tv_sec > deadline.tv_sec ||
	    (connect_deadline.tv_sec == deadline.tv_sec &&
	     connect_deadline.tv_nsec > deadline.tv_nsec))
		connect_deadline = deadline;
	source = QFW_GATEWAY_ERROR_AUTHENTICATION;
	status = qsgp_munge_encode(request_frame->data, request_frame->size,
		&request_credential, &request_credential_size);
	if (status != QSGP_OK)
		goto out;
	source = QFW_GATEWAY_ERROR_TRANSPORT;
	socket_fd = qsgp_connect_deadline(client->host, client->port,
		&connect_deadline);
	if (socket_fd < 0) {
		status = socket_fd;
		goto out;
	}
	status = qsgp_send_credential(socket_fd, request_credential,
		request_credential_size, &deadline);
	if (status != QSGP_OK)
		goto out;
	status = qsgp_receive_credential(socket_fd, &response_credential,
		&response_credential_size, client->max_credential_bytes, &deadline);
	if (status != QSGP_OK)
		goto out;
	source = QFW_GATEWAY_ERROR_AUTHENTICATION;
	status = qsgp_munge_decode(response_credential,
		response_credential_size, &response_frame, &response_frame_size,
		&identity);
	if (status != QSGP_OK)
		goto out;
	if (identity.uid != client->expected_munge_uid) {
		status = QSGP_ERR_AUTH;
		goto out;
	}
	source = QFW_GATEWAY_ERROR_PROTOCOL;
	status = qsgp_decode_header(response_frame, response_frame_size, &header);
	if (status != QSGP_OK)
		goto out;
	if (header.correlation_id != correlation) {
		status = QSGP_ERR_CONFLICT;
		goto out;
	}
	if (header.message_type == QSGP_ERROR_RESPONSE) {
		status = qsgp_decode_error_response(response_frame,
			response_frame_size, &header, &error->remote);
		if (status != QSGP_OK)
			goto out;
		error->source = QFW_GATEWAY_ERROR_REMOTE;
		error->qsgp_status = QFW_GATEWAY_ERR_REMOTE;
		status = QFW_GATEWAY_ERR_REMOTE;
		goto cleanup;
	}
	if (header.message_type != expected_response) {
		status = QSGP_ERR_INVALID;
		goto out;
	}
	if (expected_response == QSGP_RESERVE_RESPONSE)
		status = qsgp_decode_reserve_response(response_frame,
			response_frame_size, &header, response);
	else
		status = qsgp_decode_release_response(response_frame,
			response_frame_size, &header, response);
out:
	if (status != QSGP_OK)
		(void)fail(error, source, status);
cleanup:
	if (socket_fd >= 0)
		(void)close(socket_fd);
	qsgp_munge_free(request_credential);
	free(response_credential);
	qsgp_munge_free(response_frame);
	return status;
}

int qfw_gateway_reserve(const struct qfw_gateway_client *client,
	const struct qsgp_reserve_request *request,
	struct qsgp_reserve_response *response,
	struct qfw_gateway_call_error *error)
{
	struct qsgp_frame frame;
	uint64_t correlation;
	int status;

	if (client == NULL || request == NULL || response == NULL ||
	    error == NULL)
		return QSGP_ERR_INVALID;
	memset(response, 0, sizeof(*response));
	memset(error, 0, sizeof(*error));
	correlation = correlation_id(request->request_id);
	status = qsgp_encode_reserve_request(request, correlation, &frame);
	if (status != QSGP_OK)
		return fail(error, QFW_GATEWAY_ERROR_LOCAL, status);
	status = gateway_exchange(client, &frame, correlation,
		QSGP_RESERVE_RESPONSE, response, error);
	qsgp_frame_destroy(&frame);
	return status;
}

int qfw_gateway_release(const struct qfw_gateway_client *client,
	const struct qsgp_release_request *request,
	struct qsgp_release_response *response,
	struct qfw_gateway_call_error *error)
{
	struct qsgp_frame frame;
	uint64_t correlation;
	int status;

	if (client == NULL || request == NULL || response == NULL ||
	    error == NULL)
		return QSGP_ERR_INVALID;
	memset(response, 0, sizeof(*response));
	memset(error, 0, sizeof(*error));
	correlation = correlation_id(request->request_id);
	status = qsgp_encode_release_request(request, correlation, &frame);
	if (status != QSGP_OK)
		return fail(error, QFW_GATEWAY_ERROR_LOCAL, status);
	status = gateway_exchange(client, &frame, correlation,
		QSGP_RELEASE_RESPONSE, response, error);
	qsgp_frame_destroy(&frame);
	return status;
}
