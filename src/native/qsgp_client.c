#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "qfw_slurm_native.h"

static int gateway_exchange(const struct qfw_plugin_config *config,
	const struct qsgp_frame *request_frame, uint64_t correlation_id,
	uint16_t expected_response, struct qfw_gateway_result *result)
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
	int socket_fd = -1;
	int status;

	if (config == NULL || request_frame == NULL || result == NULL)
		return QSGP_ERR_INVALID;
	memset(result, 0, sizeof(*result));
	status = qsgp_deadline_after_ms(&deadline, config->request_timeout_ms);
	if (status != QSGP_OK)
		goto out;
	status = qsgp_deadline_after_ms(&connect_deadline,
		config->connect_timeout_ms);
	if (status != QSGP_OK)
		goto out;
	if (connect_deadline.tv_sec > deadline.tv_sec ||
	    (connect_deadline.tv_sec == deadline.tv_sec &&
	     connect_deadline.tv_nsec > deadline.tv_nsec))
		connect_deadline = deadline;
	status = qsgp_munge_encode(request_frame->data, request_frame->size,
		&request_credential, &request_credential_size);
	if (status != QSGP_OK)
		goto out;
	socket_fd = qsgp_connect_deadline(config->gateway_host,
		config->gateway_port, &connect_deadline);
	if (socket_fd < 0) {
		status = socket_fd;
		goto out;
	}
	status = qsgp_send_credential(socket_fd, request_credential,
		request_credential_size, &deadline);
	if (status != QSGP_OK)
		goto out;
	status = qsgp_receive_credential(socket_fd, &response_credential,
		&response_credential_size, config->max_credential_bytes, &deadline);
	if (status != QSGP_OK)
		goto out;
	status = qsgp_munge_decode(response_credential,
		response_credential_size, &response_frame, &response_frame_size,
		&identity);
	if (status != QSGP_OK)
		goto out;
	if (identity.uid != config->expected_munge_uid) {
		status = QSGP_ERR_AUTH;
		goto out;
	}
	status = qsgp_decode_header(response_frame, response_frame_size, &header);
	if (status != QSGP_OK)
		goto out;
	if (header.correlation_id != correlation_id) {
		status = QSGP_ERR_CONFLICT;
		goto out;
	}
	result->header = header;
	if (header.message_type == QSGP_ERROR_RESPONSE) {
		result->is_error = true;
		status = qsgp_decode_error_response(response_frame,
			response_frame_size, &result->header, &result->error);
		goto out;
	}
	if (header.message_type != expected_response) {
		status = QSGP_ERR_INVALID;
		goto out;
	}
	if (expected_response == QSGP_RESERVE_RESPONSE)
		status = qsgp_decode_reserve_response(response_frame,
			response_frame_size, &result->header, &result->reserve);
	else
		status = qsgp_decode_release_response(response_frame,
			response_frame_size, &result->header, &result->release);
out:
	if (socket_fd >= 0)
		(void)close(socket_fd);
	qsgp_munge_free(request_credential);
	free(response_credential);
	qsgp_munge_free(response_frame);
	return status;
}

int qfw_gateway_reserve(const struct qfw_plugin_config *config,
	const struct qsgp_reserve_request *request, uint64_t correlation_id,
	struct qfw_gateway_result *result)
{
	struct qsgp_frame frame;
	int status;

	status = qsgp_encode_reserve_request(request, correlation_id, &frame);
	if (status != QSGP_OK)
		return status;
	status = gateway_exchange(config, &frame, correlation_id,
		QSGP_RESERVE_RESPONSE, result);
	qsgp_frame_destroy(&frame);
	return status;
}

int qfw_gateway_release(const struct qfw_plugin_config *config,
	const struct qsgp_release_request *request, uint64_t correlation_id,
	struct qfw_gateway_result *result)
{
	struct qsgp_frame frame;
	int status;

	status = qsgp_encode_release_request(request, correlation_id, &frame);
	if (status != QSGP_OK)
		return status;
	status = gateway_exchange(config, &frame, correlation_id,
		QSGP_RELEASE_RESPONSE, result);
	qsgp_frame_destroy(&frame);
	return status;
}
