#include <arpa/inet.h>
#include <string.h>

#include "qsgp_internal.h"

static uint16_t get_u16(const uint8_t *source)
{
	uint16_t value;

	memcpy(&value, source, sizeof(value));
	return ntohs(value);
}
static uint32_t get_u32(const uint8_t *source)
{
	uint32_t value;

	memcpy(&value, source, sizeof(value));
	return ntohl(value);
}

static uint64_t get_u64(const uint8_t *source)
{
	uint64_t value;

	memcpy(&value, source, sizeof(value));
	return qsgp_ntoh64(value);
}

static int mark_once(uint64_t *fields, uint16_t type)
{
	uint64_t bit;

	if (type >= 64U)
		return QSGP_ERR_INVALID;
	bit = UINT64_C(1) << type;
	if ((*fields & bit) != 0)
		return QSGP_ERR_CONFLICT;
	*fields |= bit;
	return QSGP_OK;
}

static bool field_present(uint64_t fields, uint16_t type)
{
	return type < 64U && (fields & (UINT64_C(1) << type)) != 0;
}

int qsgp_decode_header(const uint8_t *data, size_t size,
	struct qsgp_header *header)
{
	uint32_t header_size;
	uint32_t reserved;

	if (data == NULL || header == NULL || size < QSGP_HEADER_SIZE ||
	    size > QSGP_MAX_FRAME_SIZE)
		return QSGP_ERR_INVALID;
	if (memcmp(data, QSGP_MAGIC, 4) != 0)
		return QSGP_ERR_INVALID;
	header->major_version = get_u16(data + 4);
	header->minor_version = get_u16(data + 6);
	header->message_type = get_u16(data + 8);
	header->flags = get_u16(data + 10);
	header_size = get_u32(data + 12);
	header->correlation_id = get_u64(data + 16);
	header->payload_size = get_u32(data + 24);
	reserved = get_u32(data + 28);
	if (header->major_version != QSGP_VERSION_MAJOR ||
	    header->minor_version > QSGP_VERSION_MINOR)
		return QSGP_ERR_VERSION;
	if (header->flags != 0 || header_size != QSGP_HEADER_SIZE ||
	    header->correlation_id == 0 || reserved != 0)
		return QSGP_ERR_INVALID;
	if ((size_t)header->payload_size != size - QSGP_HEADER_SIZE)
		return QSGP_ERR_BOUNDS;
	return QSGP_OK;
}

int qsgp_cursor_next(struct qsgp_cursor *cursor, struct qsgp_tlv *tlv)
{
	size_t padded_length;
	size_t padding;
	size_t index;

	if (cursor == NULL || tlv == NULL)
		return QSGP_ERR_INVALID;
	if (cursor->offset == cursor->size)
		return 0;
	if (cursor->offset > cursor->size ||
	    cursor->size - cursor->offset < 8U)
		return QSGP_ERR_BOUNDS;
	tlv->type = get_u16(cursor->data + cursor->offset);
	tlv->flags = get_u16(cursor->data + cursor->offset + 2U);
	tlv->length = get_u32(cursor->data + cursor->offset + 4U);
	cursor->offset += 8U;
	padding = (4U - (tlv->length % 4U)) % 4U;
	if (tlv->length > SIZE_MAX - padding)
		return QSGP_ERR_BOUNDS;
	padded_length = tlv->length + padding;
	if (padded_length > cursor->size - cursor->offset)
		return QSGP_ERR_BOUNDS;
	tlv->value = cursor->data + cursor->offset;
	for (index = tlv->length; index < padded_length; index++) {
		if (tlv->value[index] != 0)
			return QSGP_ERR_INVALID;
	}
	cursor->offset += padded_length;
	if ((tlv->flags & ~QSGP_TLV_REQUIRED) != 0)
		return QSGP_ERR_INVALID;
	if (!qsgp_known_tlv(tlv->type) &&
	    (tlv->flags & QSGP_TLV_REQUIRED) != 0)
		return QSGP_ERR_VERSION;
	return 1;
}

int qsgp_tlv_u32(const struct qsgp_tlv *tlv, uint32_t *value)
{
	if (tlv == NULL || value == NULL || tlv->length != 4U)
		return QSGP_ERR_INVALID;
	*value = get_u32(tlv->value);
	return QSGP_OK;
}

int qsgp_tlv_u64(const struct qsgp_tlv *tlv, uint64_t *value)
{
	if (tlv == NULL || value == NULL || tlv->length != 8U)
		return QSGP_ERR_INVALID;
	*value = get_u64(tlv->value);
	return QSGP_OK;
}

int qsgp_tlv_string(const struct qsgp_tlv *tlv, char *value,
	size_t capacity)
{
	if (tlv == NULL || value == NULL || capacity == 0 ||
	    tlv->length == 0 || tlv->length >= capacity ||
	    memchr(tlv->value, '\0', tlv->length) != NULL)
		return QSGP_ERR_INVALID;
	memcpy(value, tlv->value, tlv->length);
	value[tlv->length] = '\0';
	return QSGP_OK;
}

bool qsgp_known_tlv(uint16_t type)
{
	return type >= QSGP_TLV_CLUSTER_NAME &&
	       type <= QSGP_TLV_RESERVATION;
}

static int decode_start(const uint8_t *data, size_t size,
	uint16_t expected_type, struct qsgp_header *header,
	struct qsgp_cursor *cursor)
{
	int status = qsgp_decode_header(data, size, header);

	if (status != QSGP_OK)
		return status;
	if (header->message_type != expected_type)
		return QSGP_ERR_INVALID;
	cursor->data = data + QSGP_HEADER_SIZE;
	cursor->size = header->payload_size;
	cursor->offset = 0;
	return QSGP_OK;
}

static int decode_service_request(const struct qsgp_tlv *container,
	char *service_id)
{
	struct qsgp_cursor cursor = {
		.data = container->value,
		.size = container->length,
	};
	struct qsgp_tlv tlv;
	uint64_t fields = 0;
	int status;

	while ((status = qsgp_cursor_next(&cursor, &tlv)) > 0) {
		if (!qsgp_known_tlv(tlv.type))
			continue;
		if (tlv.type != QSGP_TLV_SERVICE_ID)
			return QSGP_ERR_INVALID;
		status = mark_once(&fields, tlv.type);
		if (status != QSGP_OK)
			return status;
		status = qsgp_tlv_string(&tlv, service_id,
			QSGP_MAX_SERVICE_ID + 1U);
		if (status != QSGP_OK)
			return status;
	}
	if (status < 0)
		return status;
	return field_present(fields, QSGP_TLV_SERVICE_ID) ?
		QSGP_OK : QSGP_ERR_INVALID;
}

static int decode_service_result(const struct qsgp_tlv *container,
	struct qsgp_service_result *result, bool reserve)
{
	struct qsgp_cursor cursor = {
		.data = container->value,
		.size = container->length,
	};
	struct qsgp_tlv tlv;
	uint64_t fields = 0;
	int status;

	memset(result, 0, sizeof(*result));
	while ((status = qsgp_cursor_next(&cursor, &tlv)) > 0) {
		if (!qsgp_known_tlv(tlv.type))
			continue;
		status = mark_once(&fields, tlv.type);
		if (status != QSGP_OK)
			return status;
		switch (tlv.type) {
		case QSGP_TLV_SERVICE_ID:
			status = qsgp_tlv_string(&tlv, result->service_id,
				QSGP_MAX_SERVICE_ID + 1U);
			break;
		case QSGP_TLV_ADMISSION_DECISION:
			status = qsgp_tlv_u32(&tlv, &result->decision);
			break;
		case QSGP_TLV_RESERVATION_ID:
			status = qsgp_tlv_u64(&tlv, &result->reservation_id);
			result->has_reservation_id = status == QSGP_OK;
			break;
		case QSGP_TLV_REASON_CODE:
			status = qsgp_tlv_u64(&tlv, &result->reason_code);
			break;
		case QSGP_TLV_RETRY_AFTER_NS:
			status = qsgp_tlv_u64(&tlv, &result->retry_after_ns);
			result->has_retry_after_ns = status == QSGP_OK;
			break;
		case QSGP_TLV_ESTIMATED_START_NS:
			status = qsgp_tlv_u64(&tlv,
				&result->estimated_start_ns);
			result->has_estimated_start_ns = status == QSGP_OK;
			break;
		case QSGP_TLV_ESTIMATED_FINISH_NS:
			status = qsgp_tlv_u64(&tlv,
				&result->estimated_finish_ns);
			result->has_estimated_finish_ns = status == QSGP_OK;
			break;
		case QSGP_TLV_QPM_RUNTIME_ID:
			status = qsgp_tlv_string(&tlv, result->qpm_runtime_id,
				QSGP_MAX_SERVICE_ID + 1U);
			result->has_runtime_id = status == QSGP_OK;
			break;
		case QSGP_TLV_QPM_GENERATION:
			status = qsgp_tlv_u64(&tlv, &result->qpm_generation);
			result->has_generation = status == QSGP_OK;
			break;
		case QSGP_TLV_DIAGNOSTIC:
			status = qsgp_tlv_string(&tlv, result->diagnostic,
				QSGP_MAX_DIAGNOSTIC + 1U);
			result->has_diagnostic = status == QSGP_OK;
			break;
		default:
			return QSGP_ERR_INVALID;
		}
		if (status != QSGP_OK)
			return status;
	}
	if (status < 0)
		return status;
	if (!field_present(fields, QSGP_TLV_SERVICE_ID) ||
	    !field_present(fields, QSGP_TLV_ADMISSION_DECISION) ||
	    !field_present(fields, QSGP_TLV_REASON_CODE) ||
	    result->decision < QSGP_ADMISSION_ACCEPTED ||
	    result->decision > QSGP_ADMISSION_REJECTED)
		return QSGP_ERR_INVALID;
	if (reserve && result->decision == QSGP_ADMISSION_ACCEPTED)
		return result->has_reservation_id && result->reservation_id != 0 ?
			QSGP_OK : QSGP_ERR_INVALID;
	return result->has_reservation_id ? QSGP_ERR_INVALID : QSGP_OK;
}

static int decode_release_result(const struct qsgp_tlv *container,
	struct qsgp_release_result *result)
{
	struct qsgp_cursor cursor = {
		.data = container->value,
		.size = container->length,
	};
	struct qsgp_tlv tlv;
	uint64_t fields = 0;
	int status;

	memset(result, 0, sizeof(*result));
	while ((status = qsgp_cursor_next(&cursor, &tlv)) > 0) {
		if (!qsgp_known_tlv(tlv.type))
			continue;
		status = mark_once(&fields, tlv.type);
		if (status != QSGP_OK)
			return status;
		switch (tlv.type) {
		case QSGP_TLV_SERVICE_ID:
			status = qsgp_tlv_string(&tlv, result->service_id,
				QSGP_MAX_SERVICE_ID + 1U);
			break;
		case QSGP_TLV_RESERVATION_ID:
			status = qsgp_tlv_u64(&tlv, &result->reservation_id);
			break;
		case QSGP_TLV_RESERVATION_STATE:
			status = qsgp_tlv_u32(&tlv, &result->state);
			break;
		case QSGP_TLV_GATEWAY_ERROR_CODE:
			status = qsgp_tlv_u32(&tlv, &result->gateway_error);
			result->has_gateway_error = status == QSGP_OK;
			break;
		case QSGP_TLV_DIAGNOSTIC:
			status = qsgp_tlv_string(&tlv, result->diagnostic,
				QSGP_MAX_DIAGNOSTIC + 1U);
			result->has_diagnostic = status == QSGP_OK;
			break;
		default:
			return QSGP_ERR_INVALID;
		}
		if (status != QSGP_OK)
			return status;
	}
	if (status < 0)
		return status;
	if (!field_present(fields, QSGP_TLV_SERVICE_ID) ||
	    !field_present(fields, QSGP_TLV_RESERVATION_ID) ||
	    !field_present(fields, QSGP_TLV_RESERVATION_STATE) ||
	    result->reservation_id == 0 ||
	    result->state < QSGP_RESERVATION_RELEASED ||
	    result->state > QSGP_RESERVATION_GATEWAY_FAILURE)
		return QSGP_ERR_INVALID;
	return QSGP_OK;
}

static int decode_reservation(const struct qsgp_tlv *container,
	struct qsgp_reservation *reservation)
{
	struct qsgp_cursor cursor = {
		.data = container->value,
		.size = container->length,
	};
	struct qsgp_tlv tlv;
	uint64_t fields = 0;
	int status;

	memset(reservation, 0, sizeof(*reservation));
	while ((status = qsgp_cursor_next(&cursor, &tlv)) > 0) {
		if (!qsgp_known_tlv(tlv.type))
			continue;
		status = mark_once(&fields, tlv.type);
		if (status != QSGP_OK)
			return status;
		if (tlv.type == QSGP_TLV_SERVICE_ID)
			status = qsgp_tlv_string(&tlv,
				reservation->service_id,
				QSGP_MAX_SERVICE_ID + 1U);
		else if (tlv.type == QSGP_TLV_RESERVATION_ID)
			status = qsgp_tlv_u64(&tlv,
				&reservation->reservation_id);
		else
			return QSGP_ERR_INVALID;
		if (status != QSGP_OK)
			return status;
	}
	if (status < 0)
		return status;
	if (!field_present(fields, QSGP_TLV_SERVICE_ID) ||
	    !field_present(fields, QSGP_TLV_RESERVATION_ID) ||
	    reservation->reservation_id == 0)
		return QSGP_ERR_INVALID;
	return QSGP_OK;
}

static int decode_admission_request(const uint8_t *data, size_t size,
	uint16_t message_type, struct qsgp_header *header,
	struct qsgp_reserve_request *request)
{
	struct qsgp_cursor cursor;
	struct qsgp_tlv tlv;
	uint64_t fields = 0;
	uint32_t value32;
	size_t index;
	size_t other;
	int status;

	if (request == NULL)
		return QSGP_ERR_INVALID;
	memset(request, 0, sizeof(*request));
	status = decode_start(data, size, message_type, header, &cursor);
	if (status != QSGP_OK)
		return status;
	while ((status = qsgp_cursor_next(&cursor, &tlv)) > 0) {
		if (!qsgp_known_tlv(tlv.type))
			continue;
		if (tlv.type == QSGP_TLV_SERVICE_REQUEST) {
			if (request->service_count >= QSGP_MAX_SERVICES)
				return QSGP_ERR_BOUNDS;
			status = decode_service_request(&tlv,
				request->service_ids[request->service_count]);
			if (status != QSGP_OK)
				return status;
			request->service_count++;
			continue;
		}
		status = mark_once(&fields, tlv.type);
		if (status != QSGP_OK)
			return status;
		switch (tlv.type) {
		case QSGP_TLV_REQUEST_ID:
			status = qsgp_tlv_u64(&tlv, &request->request_id);
			break;
		case QSGP_TLV_CLUSTER_NAME:
			status = qsgp_tlv_string(&tlv, request->cluster_name,
				QSGP_MAX_CLUSTER_NAME + 1U);
			break;
		case QSGP_TLV_CANONICAL_JOB_ID:
			status = qsgp_tlv_u64(&tlv,
				&request->canonical_job_id);
			break;
		case QSGP_TLV_HETERO_JOB_ID:
			status = qsgp_tlv_u64(&tlv, &request->hetero_job_id);
			request->has_hetero_job_id = status == QSGP_OK;
			break;
		case QSGP_TLV_HETERO_COMPONENT:
			status = qsgp_tlv_u32(&tlv,
				&request->hetero_component);
			request->has_hetero_component = status == QSGP_OK;
			break;
		case QSGP_TLV_JOB_UID:
			status = qsgp_tlv_u32(&tlv, &value32);
			request->job_uid = (uid_t)value32;
			break;
		case QSGP_TLV_JOB_GID:
			status = qsgp_tlv_u32(&tlv, &value32);
			request->job_gid = (gid_t)value32;
			break;
		case QSGP_TLV_WORKLOAD_KIND:
			status = qsgp_tlv_u32(&tlv, &request->workload.kind);
			break;
		case QSGP_TLV_WALLTIME_NS:
			status = qsgp_tlv_u64(&tlv,
				&request->workload.walltime_ns);
			break;
		case QSGP_TLV_CIRCUIT_COUNT:
			status = qsgp_tlv_u64(&tlv,
				&request->workload.circuit_count);
			break;
		case QSGP_TLV_MAX_QUBITS:
			status = qsgp_tlv_u32(&tlv,
				&request->workload.max_qubits);
			break;
		case QSGP_TLV_MAX_DEPTH:
			status = qsgp_tlv_u64(&tlv,
				&request->workload.max_depth);
			break;
		case QSGP_TLV_MAX_SHOTS:
			status = qsgp_tlv_u64(&tlv,
				&request->workload.max_shots);
			break;
		case QSGP_TLV_MAX_ONE_Q_GATES:
			status = qsgp_tlv_u64(&tlv,
				&request->workload.max_one_q_gates);
			request->workload.has_max_one_q_gates =
				status == QSGP_OK;
			break;
		case QSGP_TLV_MAX_TWO_Q_GATES:
			status = qsgp_tlv_u64(&tlv,
				&request->workload.max_two_q_gates);
			request->workload.has_max_two_q_gates =
				status == QSGP_OK;
			break;
		case QSGP_TLV_MAX_MEASUREMENTS:
			status = qsgp_tlv_u64(&tlv,
				&request->workload.max_measurements);
			request->workload.has_max_measurements =
				status == QSGP_OK;
			break;
		default:
			return QSGP_ERR_INVALID;
		}
		if (status != QSGP_OK)
			return status;
	}
	if (status < 0)
		return status;
#define REQUIRED(type) field_present(fields, (type))
	request->has_workload = REQUIRED(QSGP_TLV_WORKLOAD_KIND);
	if (request->has_workload != REQUIRED(QSGP_TLV_WALLTIME_NS) ||
	    request->has_workload != REQUIRED(QSGP_TLV_CIRCUIT_COUNT) ||
	    request->has_workload != REQUIRED(QSGP_TLV_MAX_QUBITS) ||
	    request->has_workload != REQUIRED(QSGP_TLV_MAX_DEPTH) ||
	    request->has_workload != REQUIRED(QSGP_TLV_MAX_SHOTS))
		return QSGP_ERR_INVALID;
	if (!request->has_workload &&
	    (REQUIRED(QSGP_TLV_MAX_ONE_Q_GATES) ||
	     REQUIRED(QSGP_TLV_MAX_TWO_Q_GATES) ||
	     REQUIRED(QSGP_TLV_MAX_MEASUREMENTS)))
		return QSGP_ERR_INVALID;
	if (request->has_hetero_job_id != request->has_hetero_component)
		return QSGP_ERR_INVALID;
	for (index = 0; index < request->service_count; index++) {
		for (other = 0; other < index; other++) {
			if (strcmp(request->service_ids[index],
				request->service_ids[other]) == 0)
				return QSGP_ERR_CONFLICT;
		}
	}
	if (!REQUIRED(QSGP_TLV_REQUEST_ID) ||
	    !REQUIRED(QSGP_TLV_CLUSTER_NAME) ||
	    !REQUIRED(QSGP_TLV_CANONICAL_JOB_ID) ||
	    !REQUIRED(QSGP_TLV_JOB_UID) || !REQUIRED(QSGP_TLV_JOB_GID) ||
	    request->request_id == 0 ||
	    request->canonical_job_id == 0 || request->service_count == 0 ||
	    (request->has_workload &&
	     (request->workload.kind < QSGP_WORKLOAD_QUANTUM ||
	      request->workload.kind > QSGP_WORKLOAD_HYBRID ||
	      request->workload.walltime_ns == 0 ||
	      request->workload.circuit_count == 0 ||
	      request->workload.max_qubits == 0 ||
	      request->workload.max_depth == 0 ||
	      request->workload.max_shots == 0)))
		return QSGP_ERR_INVALID;
	return QSGP_OK;
#undef REQUIRED
}

int qsgp_decode_reserve_request(const uint8_t *data, size_t size,
	struct qsgp_header *header, struct qsgp_reserve_request *request)
{
	return decode_admission_request(data, size, QSGP_RESERVE_REQUEST,
		header, request);
}

int qsgp_decode_evaluate_request(const uint8_t *data, size_t size,
	struct qsgp_header *header, struct qsgp_reserve_request *request)
{
	int status = decode_admission_request(data, size,
		QSGP_EVALUATE_REQUEST, header, request);

	if (status != QSGP_OK)
		return status;
	return request->has_workload ? QSGP_OK : QSGP_ERR_INVALID;
}

static int decode_admission_response(const uint8_t *data, size_t size,
	uint16_t message_type, bool reserve, struct qsgp_header *header,
	struct qsgp_reserve_response *response)
{
	struct qsgp_cursor cursor;
	struct qsgp_tlv tlv;
	uint64_t fields = 0;
	bool found_delayed = false;
	bool found_rejected = false;
	size_t index;
	int status;

	if (response == NULL)
		return QSGP_ERR_INVALID;
	memset(response, 0, sizeof(*response));
	status = decode_start(data, size, message_type, header, &cursor);
	if (status != QSGP_OK)
		return status;
	while ((status = qsgp_cursor_next(&cursor, &tlv)) > 0) {
		if (!qsgp_known_tlv(tlv.type))
			continue;
		if (tlv.type == QSGP_TLV_SERVICE_RESULT) {
			if (response->result_count >= QSGP_MAX_SERVICES)
				return QSGP_ERR_BOUNDS;
			status = decode_service_result(&tlv,
				&response->results[response->result_count], reserve);
			if (status != QSGP_OK)
				return status;
			response->result_count++;
			continue;
		}
		status = mark_once(&fields, tlv.type);
		if (status != QSGP_OK)
			return status;
		if (tlv.type == QSGP_TLV_REQUEST_ID)
			status = qsgp_tlv_u64(&tlv, &response->request_id);
		else if (tlv.type == QSGP_TLV_ADMISSION_DECISION)
			status = qsgp_tlv_u32(&tlv, &response->decision);
		else
			return QSGP_ERR_INVALID;
		if (status != QSGP_OK)
			return status;
	}
	if (status < 0)
		return status;
	if (!field_present(fields, QSGP_TLV_REQUEST_ID) ||
	    !field_present(fields, QSGP_TLV_ADMISSION_DECISION) ||
	    response->request_id == 0 || response->result_count == 0 ||
	    response->decision < QSGP_ADMISSION_ACCEPTED ||
	    response->decision > QSGP_ADMISSION_REJECTED)
		return QSGP_ERR_INVALID;
	for (index = 0; index < response->result_count; index++) {
		size_t other;

		for (other = 0; other < index; other++) {
			if (strcmp(response->results[index].service_id,
				response->results[other].service_id) == 0)
				return QSGP_ERR_CONFLICT;
		}
		if (response->results[index].decision == QSGP_ADMISSION_DELAYED)
			found_delayed = true;
		if (response->results[index].decision == QSGP_ADMISSION_REJECTED)
			found_rejected = true;
		if (response->decision == QSGP_ADMISSION_ACCEPTED &&
		    response->results[index].decision != QSGP_ADMISSION_ACCEPTED)
			return QSGP_ERR_INVALID;
	}
	if (response->decision == QSGP_ADMISSION_REJECTED && !found_rejected)
		return QSGP_ERR_INVALID;
	if (response->decision == QSGP_ADMISSION_DELAYED &&
	    (found_rejected || !found_delayed))
		return QSGP_ERR_INVALID;
	return QSGP_OK;
}

int qsgp_decode_reserve_response(const uint8_t *data, size_t size,
	struct qsgp_header *header, struct qsgp_reserve_response *response)
{
	return decode_admission_response(data, size, QSGP_RESERVE_RESPONSE,
		true, header, response);
}

int qsgp_decode_evaluate_response(const uint8_t *data, size_t size,
	struct qsgp_header *header, struct qsgp_reserve_response *response)
{
	return decode_admission_response(data, size, QSGP_EVALUATE_RESPONSE,
		false, header, response);
}

int qsgp_decode_release_request(const uint8_t *data, size_t size,
	struct qsgp_header *header, struct qsgp_release_request *request)
{
	struct qsgp_cursor cursor;
	struct qsgp_tlv tlv;
	uint64_t fields = 0;
	int status;

	if (request == NULL)
		return QSGP_ERR_INVALID;
	memset(request, 0, sizeof(*request));
	status = decode_start(data, size, QSGP_RELEASE_REQUEST, header, &cursor);
	if (status != QSGP_OK)
		return status;
	while ((status = qsgp_cursor_next(&cursor, &tlv)) > 0) {
		if (!qsgp_known_tlv(tlv.type))
			continue;
		status = mark_once(&fields, tlv.type);
		if (status != QSGP_OK)
			return status;
		switch (tlv.type) {
		case QSGP_TLV_REQUEST_ID:
			status = qsgp_tlv_u64(&tlv, &request->request_id);
			break;
		case QSGP_TLV_CLUSTER_NAME:
			status = qsgp_tlv_string(&tlv, request->cluster_name,
				QSGP_MAX_CLUSTER_NAME + 1U);
			break;
		case QSGP_TLV_CANONICAL_JOB_ID:
			status = qsgp_tlv_u64(&tlv,
				&request->canonical_job_id);
			break;
		case QSGP_TLV_RELEASE_REASON:
			status = qsgp_tlv_u32(&tlv, &request->reason);
			break;
		default:
			return QSGP_ERR_INVALID;
		}
		if (status != QSGP_OK)
			return status;
	}
	if (status < 0)
		return status;
	if (!field_present(fields, QSGP_TLV_REQUEST_ID) ||
	    !field_present(fields, QSGP_TLV_CLUSTER_NAME) ||
	    !field_present(fields, QSGP_TLV_CANONICAL_JOB_ID) ||
	    !field_present(fields, QSGP_TLV_RELEASE_REASON) ||
	    request->request_id == 0 || request->canonical_job_id == 0)
		return QSGP_ERR_INVALID;
	return QSGP_OK;
}

int qsgp_decode_release_response(const uint8_t *data, size_t size,
	struct qsgp_header *header, struct qsgp_release_response *response)
{
	struct qsgp_cursor cursor;
	struct qsgp_tlv tlv;
	uint64_t fields = 0;
	int status;

	if (response == NULL)
		return QSGP_ERR_INVALID;
	memset(response, 0, sizeof(*response));
	status = decode_start(data, size, QSGP_RELEASE_RESPONSE, header, &cursor);
	if (status != QSGP_OK)
		return status;
	while ((status = qsgp_cursor_next(&cursor, &tlv)) > 0) {
		if (!qsgp_known_tlv(tlv.type))
			continue;
		if (tlv.type == QSGP_TLV_RELEASE_RESULT) {
			if (response->result_count >= QSGP_MAX_SERVICES)
				return QSGP_ERR_BOUNDS;
			status = decode_release_result(&tlv,
				&response->results[response->result_count]);
			if (status != QSGP_OK)
				return status;
			response->result_count++;
			continue;
		}
		status = mark_once(&fields, tlv.type);
		if (status != QSGP_OK)
			return status;
		if (tlv.type != QSGP_TLV_REQUEST_ID)
			return QSGP_ERR_INVALID;
		status = qsgp_tlv_u64(&tlv, &response->request_id);
		if (status != QSGP_OK)
			return status;
	}
	if (status < 0)
		return status;
	if (!field_present(fields, QSGP_TLV_REQUEST_ID) ||
	    response->request_id == 0)
		return QSGP_ERR_INVALID;
	return QSGP_OK;
}

int qsgp_decode_get_reservations_request(const uint8_t *data, size_t size,
	struct qsgp_header *header,
	struct qsgp_get_reservations_request *request)
{
	struct qsgp_cursor cursor;
	struct qsgp_tlv tlv;
	uint64_t fields = 0;
	uint32_t value32;
	int status;

	if (request == NULL)
		return QSGP_ERR_INVALID;
	memset(request, 0, sizeof(*request));
	status = decode_start(data, size, QSGP_GET_RESERVATIONS_REQUEST,
		header, &cursor);
	if (status != QSGP_OK)
		return status;
	while ((status = qsgp_cursor_next(&cursor, &tlv)) > 0) {
		if (!qsgp_known_tlv(tlv.type))
			continue;
		status = mark_once(&fields, tlv.type);
		if (status != QSGP_OK)
			return status;
		switch (tlv.type) {
		case QSGP_TLV_REQUEST_ID:
			status = qsgp_tlv_u64(&tlv, &request->request_id);
			break;
		case QSGP_TLV_CLUSTER_NAME:
			status = qsgp_tlv_string(&tlv, request->cluster_name,
				QSGP_MAX_CLUSTER_NAME + 1U);
			break;
		case QSGP_TLV_OBSERVED_JOB_ID:
			status = qsgp_tlv_u64(&tlv,
				&request->observed_job_id);
			break;
		case QSGP_TLV_JOB_UID:
			status = qsgp_tlv_u32(&tlv, &value32);
			request->job_uid = (uid_t)value32;
			break;
		case QSGP_TLV_JOB_GID:
			status = qsgp_tlv_u32(&tlv, &value32);
			request->job_gid = (gid_t)value32;
			break;
		default:
			return QSGP_ERR_INVALID;
		}
		if (status != QSGP_OK)
			return status;
	}
	if (status < 0)
		return status;
	if (!field_present(fields, QSGP_TLV_REQUEST_ID) ||
	    !field_present(fields, QSGP_TLV_CLUSTER_NAME) ||
	    !field_present(fields, QSGP_TLV_OBSERVED_JOB_ID) ||
	    !field_present(fields, QSGP_TLV_JOB_UID) ||
	    !field_present(fields, QSGP_TLV_JOB_GID) ||
	    request->request_id == 0 || request->observed_job_id == 0)
		return QSGP_ERR_INVALID;
	return QSGP_OK;
}

int qsgp_decode_get_reservations_response(const uint8_t *data, size_t size,
	struct qsgp_header *header,
	struct qsgp_get_reservations_response *response)
{
	struct qsgp_cursor cursor;
	struct qsgp_tlv tlv;
	uint64_t fields = 0;
	size_t index;
	int status;

	if (response == NULL)
		return QSGP_ERR_INVALID;
	memset(response, 0, sizeof(*response));
	status = decode_start(data, size, QSGP_GET_RESERVATIONS_RESPONSE,
		header, &cursor);
	if (status != QSGP_OK)
		return status;
	while ((status = qsgp_cursor_next(&cursor, &tlv)) > 0) {
		if (!qsgp_known_tlv(tlv.type))
			continue;
		if (tlv.type == QSGP_TLV_RESERVATION) {
			if (response->reservation_count >= QSGP_MAX_SERVICES)
				return QSGP_ERR_BOUNDS;
			status = decode_reservation(&tlv,
				&response->reservations[response->reservation_count]);
			if (status != QSGP_OK)
				return status;
			response->reservation_count++;
			continue;
		}
		status = mark_once(&fields, tlv.type);
		if (status != QSGP_OK)
			return status;
		if (tlv.type == QSGP_TLV_REQUEST_ID)
			status = qsgp_tlv_u64(&tlv, &response->request_id);
		else if (tlv.type == QSGP_TLV_CANONICAL_JOB_ID)
			status = qsgp_tlv_u64(&tlv,
				&response->canonical_job_id);
		else
			return QSGP_ERR_INVALID;
		if (status != QSGP_OK)
			return status;
	}
	if (status < 0)
		return status;
	if (!field_present(fields, QSGP_TLV_REQUEST_ID) ||
	    !field_present(fields, QSGP_TLV_CANONICAL_JOB_ID) ||
	    response->request_id == 0 || response->canonical_job_id == 0 ||
	    response->reservation_count == 0)
		return QSGP_ERR_INVALID;
	for (index = 0; index < response->reservation_count; index++) {
		size_t other;

		for (other = 0; other < index; other++) {
			if (strcmp(response->reservations[index].service_id,
				response->reservations[other].service_id) == 0)
				return QSGP_ERR_CONFLICT;
		}
	}
	return QSGP_OK;
}

int qsgp_decode_error_response(const uint8_t *data, size_t size,
	struct qsgp_header *header, struct qsgp_error_response *response)
{
	struct qsgp_cursor cursor;
	struct qsgp_tlv tlv;
	uint64_t fields = 0;
	int status;

	if (response == NULL)
		return QSGP_ERR_INVALID;
	memset(response, 0, sizeof(*response));
	status = decode_start(data, size, QSGP_ERROR_RESPONSE, header, &cursor);
	if (status != QSGP_OK)
		return status;
	while ((status = qsgp_cursor_next(&cursor, &tlv)) > 0) {
		if (!qsgp_known_tlv(tlv.type))
			continue;
		status = mark_once(&fields, tlv.type);
		if (status != QSGP_OK)
			return status;
		switch (tlv.type) {
		case QSGP_TLV_REQUEST_ID:
			status = qsgp_tlv_u64(&tlv, &response->request_id);
			response->has_request_id = status == QSGP_OK;
			break;
		case QSGP_TLV_GATEWAY_ERROR_CODE:
			status = qsgp_tlv_u32(&tlv, &response->error_code);
			break;
		case QSGP_TLV_DIAGNOSTIC:
			status = qsgp_tlv_string(&tlv, response->diagnostic,
				QSGP_MAX_DIAGNOSTIC + 1U);
			response->has_diagnostic = status == QSGP_OK;
			break;
		default:
			return QSGP_ERR_INVALID;
		}
		if (status != QSGP_OK)
			return status;
	}
	if (status < 0)
		return status;
	if (!field_present(fields, QSGP_TLV_GATEWAY_ERROR_CODE) ||
	    response->error_code == 0)
		return QSGP_ERR_INVALID;
	return QSGP_OK;
}

const char *qsgp_status_string(int status)
{
	switch (status) {
	case QSGP_OK:
		return "success";
	case QSGP_ERR_INVALID:
		return "invalid data";
	case QSGP_ERR_NOMEM:
		return "out of memory";
	case QSGP_ERR_BOUNDS:
		return "size limit exceeded";
	case QSGP_ERR_VERSION:
		return "unsupported protocol version";
	case QSGP_ERR_IO:
		return "I/O failure";
	case QSGP_ERR_AUTH:
		return "authentication failure";
	case QSGP_ERR_TIMEOUT:
		return "operation timed out";
	case QSGP_ERR_CONFLICT:
		return "conflicting duplicate field";
	default:
		return "unknown error";
	}
}
