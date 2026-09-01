#include <stdio.h>
#include <string.h>

#include "qfw_slurm_native.h"

static void set_diagnostic(char *output, size_t output_size,
	const char *message)
{
	if (output != NULL && output_size != 0)
		(void)snprintf(output, output_size, "%s",
			message != NULL ? message : "operation failed");
}

static bool expected_service(const struct qsgp_reserve_request *request,
	const char *service_id)
{
	size_t index;

	for (index = 0; index < request->service_count; index++) {
		if (strcmp(request->service_ids[index], service_id) == 0)
			return true;
	}
	return false;
}

static bool duplicate_result(const struct qsgp_reserve_response *response,
	size_t current)
{
	size_t index;

	for (index = 0; index < current; index++) {
		if (strcmp(response->results[index].service_id,
			response->results[current].service_id) == 0)
			return true;
	}
	return false;
}

static int response_error(struct qfw_reserve_operation_result *result,
	const char *message)
{
	result->state = QFW_OPERATION_RESPONSE_ERROR;
	set_diagnostic(result->diagnostic, sizeof(result->diagnostic), message);
	return 0;
}

int qfw_reserve_response_process(
	const struct qsgp_reserve_request *request,
	const struct qsgp_reserve_response *response,
	struct qfw_reserve_operation_result *result)
{
	const struct qsgp_service_result *decisive = NULL;
	size_t index;

	if (request == NULL || response == NULL || result == NULL)
		return -1;
	result->response = *response;
	if (response->request_id != request->request_id)
		return response_error(result,
			"gateway response request identity does not match");
	if (response->decision < QSGP_ADMISSION_ACCEPTED ||
	    response->decision > QSGP_ADMISSION_REJECTED)
		return response_error(result,
			"gateway returned an unknown admission decision");
	if (response->result_count == 0 ||
	    response->result_count > QSGP_MAX_SERVICES)
		return response_error(result,
			"gateway returned an empty or oversized service result set");
	for (index = 0; index < response->result_count; index++) {
		const struct qsgp_service_result *item =
			&response->results[index];

		if (item->service_id[0] == '\0' ||
		    !expected_service(request, item->service_id))
			return response_error(result,
				"gateway returned an unexpected service result");
		if (duplicate_result(response, index))
			return response_error(result,
				"gateway returned a duplicate service result");
		if (item->decision < QSGP_ADMISSION_ACCEPTED ||
		    item->decision > QSGP_ADMISSION_REJECTED)
			return response_error(result,
				"gateway returned an unknown service decision");
		if (item->decision == response->decision && decisive == NULL)
			decisive = item;
	}
	if (response->decision == QSGP_ADMISSION_ACCEPTED) {
		if (response->result_count != request->service_count)
			return response_error(result,
				"gateway returned an incomplete service result set");
		for (index = 0; index < response->result_count; index++) {
			const struct qsgp_service_result *item =
				&response->results[index];

			if (item->decision != QSGP_ADMISSION_ACCEPTED ||
			    !item->has_reservation_id ||
			    item->reservation_id == 0)
				return response_error(result,
					"accepted service has no valid reservation");
		}
		if (qfw_reservations_json(response, result->reservations_json,
			sizeof(result->reservations_json)) != 0)
			return response_error(result,
				"cannot format the accepted reservation set");
		result->state = QFW_OPERATION_ACCEPTED;
		return 0;
	}
	if (decisive == NULL)
		return response_error(result,
			"admission decision has no corresponding service result");
	if (decisive->has_diagnostic)
		set_diagnostic(result->diagnostic, sizeof(result->diagnostic),
			decisive->diagnostic);
	else if (response->decision == QSGP_ADMISSION_DELAYED)
		set_diagnostic(result->diagnostic, sizeof(result->diagnostic),
			"QPM reservation was delayed");
	else
		set_diagnostic(result->diagnostic, sizeof(result->diagnostic),
			"QPM reservation was rejected");
	result->state = response->decision == QSGP_ADMISSION_DELAYED ?
		QFW_OPERATION_DELAYED : QFW_OPERATION_REJECTED;
	return 0;
}

static void map_call_failure(uint32_t *state, char *diagnostic,
	size_t diagnostic_size, const struct qfw_gateway_call_error *error)
{
	*state = error->source == QFW_GATEWAY_ERROR_REMOTE ?
		QFW_OPERATION_GATEWAY_ERROR : QFW_OPERATION_CLIENT_ERROR;
	set_diagnostic(diagnostic, diagnostic_size,
		qfw_gateway_call_error_message(error));
}

int qfw_reserve_operation(const struct qfw_gateway_client *client,
	const struct qfw_plugin_config *config,
	const struct qfw_quantum_options *options,
	const struct qfw_allocation_context *allocation,
	struct qfw_reserve_operation_result *result)
{
	int status;

	if (client == NULL || config == NULL || options == NULL ||
	    allocation == NULL || result == NULL)
		return -1;
	memset(result, 0, sizeof(*result));
	status = qfw_build_reserve_request(config, options,
		allocation->cluster_name, allocation->canonical_job_id,
		allocation->allocation_epoch, allocation->job_uid,
		allocation->job_gid, allocation->has_hetero,
		allocation->hetero_job_id, allocation->hetero_component,
		allocation->walltime_ns, &result->request,
		result->diagnostic, sizeof(result->diagnostic));
	if (status != 0) {
		result->state = QFW_OPERATION_INVALID;
		return 0;
	}
	status = qfw_gateway_reserve(client, &result->request,
		&result->response, &result->call_error);
	if (status != QFW_GATEWAY_OK) {
		map_call_failure(&result->state, result->diagnostic,
			sizeof(result->diagnostic), &result->call_error);
		return 0;
	}
	return qfw_reserve_response_process(&result->request,
		&result->response, result);
}

static bool terminal_release_state(uint32_t state)
{
	return state == QSGP_RESERVATION_RELEASED ||
		state == QSGP_RESERVATION_ALREADY_TERMINAL ||
		state == QSGP_RESERVATION_NOT_FOUND ||
		state == QSGP_RESERVATION_STALE_RUNTIME;
}

static bool unresolved_release_state(uint32_t state)
{
	return state == QSGP_RESERVATION_AUTHORIZATION_FAILURE ||
		state == QSGP_RESERVATION_QPM_FAILURE ||
		state == QSGP_RESERVATION_GATEWAY_FAILURE;
}

int qfw_release_response_process(
	const struct qsgp_release_request *request,
	const struct qsgp_release_response *response,
	struct qfw_release_operation_result *result)
{
	size_t index;

	if (request == NULL || response == NULL || result == NULL)
		return -1;
	result->response = *response;
	if (response->request_id != request->request_id) {
		result->state = QFW_OPERATION_RESPONSE_ERROR;
		set_diagnostic(result->diagnostic, sizeof(result->diagnostic),
			"gateway response request identity does not match");
		return 0;
	}
	if (response->result_count > QSGP_MAX_SERVICES) {
		result->state = QFW_OPERATION_RESPONSE_ERROR;
		set_diagnostic(result->diagnostic, sizeof(result->diagnostic),
			"gateway returned an oversized release result set");
		return 0;
	}
	for (index = 0; index < response->result_count; index++) {
		const struct qsgp_release_result *item =
			&response->results[index];

		if (terminal_release_state(item->state))
			continue;
		if (!unresolved_release_state(item->state)) {
			result->state = QFW_OPERATION_RESPONSE_ERROR;
			set_diagnostic(result->diagnostic,
				sizeof(result->diagnostic),
				"gateway returned an unknown release state");
			return 0;
		}
		result->unresolved_count++;
		if (result->diagnostic[0] == '\0' && item->has_diagnostic)
			set_diagnostic(result->diagnostic,
				sizeof(result->diagnostic), item->diagnostic);
	}
	result->state = result->unresolved_count == 0 ?
		QFW_OPERATION_RELEASED : QFW_OPERATION_RELEASE_UNRESOLVED;
	if (result->unresolved_count != 0 && result->diagnostic[0] == '\0')
		set_diagnostic(result->diagnostic, sizeof(result->diagnostic),
			"one or more reservations remain unresolved");
	return 0;
}

int qfw_release_operation(const struct qfw_gateway_client *client,
	const struct qfw_allocation_context *allocation, uint32_t reason,
	struct qfw_release_operation_result *result)
{
	int status;

	if (client == NULL || allocation == NULL || result == NULL)
		return -1;
	memset(result, 0, sizeof(*result));
	if (allocation->cluster_name[0] == '\0' ||
	    allocation->canonical_job_id == 0) {
		result->state = QFW_OPERATION_INVALID;
		set_diagnostic(result->diagnostic, sizeof(result->diagnostic),
			"release allocation identity is invalid");
		return 0;
	}
	result->request.canonical_job_id = allocation->canonical_job_id;
	result->request.reason = reason;
	(void)snprintf(result->request.cluster_name,
		sizeof(result->request.cluster_name), "%s",
		allocation->cluster_name);
	result->request.request_id = qfw_request_id(allocation->cluster_name,
		allocation->canonical_job_id, allocation->allocation_epoch,
		QSGP_RELEASE_REQUEST);
	status = qfw_gateway_release(client, &result->request,
		&result->response, &result->call_error);
	if (status != QFW_GATEWAY_OK) {
		map_call_failure(&result->state, result->diagnostic,
			sizeof(result->diagnostic), &result->call_error);
		return 0;
	}
	return qfw_release_response_process(&result->request,
		&result->response, result);
}
