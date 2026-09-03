#include <arpa/inet.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

#include "qsgp_internal.h"

#define QSGP_INITIAL_CAPACITY 512U

static size_t bounded_strlen(const char *value, size_t maximum)
{
	size_t length;

	if (value == NULL)
		return maximum + 1U;
	for (length = 0; length <= maximum; length++) {
		if (value[length] == '\0')
			return length;
	}
	return maximum + 1U;
}

static void put_u16(uint8_t *destination, uint16_t value)
{
	uint16_t encoded = htons(value);

	memcpy(destination, &encoded, sizeof(encoded));
}

static void put_u32(uint8_t *destination, uint32_t value)
{
	uint32_t encoded = htonl(value);

	memcpy(destination, &encoded, sizeof(encoded));
}

static void put_u64(uint8_t *destination, uint64_t value)
{
	uint64_t encoded = qsgp_hton64(value);

	memcpy(destination, &encoded, sizeof(encoded));
}

static int builder_reserve(struct qsgp_builder *builder, size_t additional)
{
	size_t capacity;
	uint8_t *new_data;

	if (additional > QSGP_MAX_FRAME_SIZE ||
	    builder->size > QSGP_MAX_FRAME_SIZE - additional)
		return QSGP_ERR_BOUNDS;
	if (builder->size + additional <= builder->capacity)
		return QSGP_OK;

	capacity = builder->capacity;
	if (capacity == 0)
		capacity = QSGP_INITIAL_CAPACITY;
	while (capacity < builder->size + additional) {
		if (capacity > QSGP_MAX_FRAME_SIZE / 2U) {
			capacity = QSGP_MAX_FRAME_SIZE;
			break;
		}
		capacity *= 2U;
	}
	new_data = realloc(builder->data, capacity);
	if (new_data == NULL)
		return QSGP_ERR_NOMEM;
	builder->data = new_data;
	builder->capacity = capacity;
	return QSGP_OK;
}

int qsgp_builder_init(struct qsgp_builder *builder, size_t initial_size)
{
	int status;

	if (builder == NULL || initial_size > QSGP_MAX_FRAME_SIZE)
		return QSGP_ERR_INVALID;
	memset(builder, 0, sizeof(*builder));
	status = builder_reserve(builder, initial_size);
	if (status != QSGP_OK)
		return status;
	memset(builder->data, 0, initial_size);
	builder->size = initial_size;
	return QSGP_OK;
}

void qsgp_builder_destroy(struct qsgp_builder *builder)
{
	if (builder == NULL)
		return;
	free(builder->data);
	memset(builder, 0, sizeof(*builder));
}

int qsgp_builder_add_raw(struct qsgp_builder *builder, const void *data,
	size_t size)
{
	int status;

	if (builder == NULL || (data == NULL && size != 0))
		return QSGP_ERR_INVALID;
	status = builder_reserve(builder, size);
	if (status != QSGP_OK)
		return status;
	if (size != 0)
		memcpy(builder->data + builder->size, data, size);
	builder->size += size;
	return QSGP_OK;
}

int qsgp_builder_add_tlv(struct qsgp_builder *builder, uint16_t type,
	uint16_t flags, const void *value, size_t size)
{
	uint8_t tlv_header[8];
	uint8_t padding[3] = {0, 0, 0};
	size_t padding_size;
	int status;

	if (size > UINT32_MAX || (value == NULL && size != 0))
		return QSGP_ERR_INVALID;
	put_u16(tlv_header, type);
	put_u16(tlv_header + 2, flags);
	put_u32(tlv_header + 4, (uint32_t)size);
	status = qsgp_builder_add_raw(builder, tlv_header, sizeof(tlv_header));
	if (status != QSGP_OK)
		return status;
	status = qsgp_builder_add_raw(builder, value, size);
	if (status != QSGP_OK)
		return status;
	padding_size = (4U - (size % 4U)) % 4U;
	return qsgp_builder_add_raw(builder, padding, padding_size);
}

int qsgp_builder_add_u32(struct qsgp_builder *builder, uint16_t type,
	uint32_t value)
{
	uint8_t encoded[4];

	put_u32(encoded, value);
	return qsgp_builder_add_tlv(builder, type, QSGP_TLV_REQUIRED,
		encoded, sizeof(encoded));
}

int qsgp_builder_add_u64(struct qsgp_builder *builder, uint16_t type,
	uint64_t value)
{
	uint8_t encoded[8];

	put_u64(encoded, value);
	return qsgp_builder_add_tlv(builder, type, QSGP_TLV_REQUIRED,
		encoded, sizeof(encoded));
}

int qsgp_builder_add_string(struct qsgp_builder *builder, uint16_t type,
	const char *value, size_t maximum_size)
{
	size_t length = bounded_strlen(value, maximum_size);

	if (length == 0 || length > maximum_size)
		return QSGP_ERR_INVALID;
	return qsgp_builder_add_tlv(builder, type, QSGP_TLV_REQUIRED,
		value, length);
}

int qsgp_builder_finish(struct qsgp_builder *builder, uint16_t message_type,
	uint64_t correlation_id, struct qsgp_frame *frame)
{
	size_t payload_size;

	if (builder == NULL || frame == NULL || correlation_id == 0 ||
	    builder->size < QSGP_HEADER_SIZE)
		return QSGP_ERR_INVALID;
	payload_size = builder->size - QSGP_HEADER_SIZE;
	if (payload_size > UINT32_MAX)
		return QSGP_ERR_BOUNDS;
	memcpy(builder->data, QSGP_MAGIC, 4);
	put_u16(builder->data + 4, QSGP_VERSION_MAJOR);
	put_u16(builder->data + 6, QSGP_VERSION_MINOR);
	put_u16(builder->data + 8, message_type);
	put_u16(builder->data + 10, 0);
	put_u32(builder->data + 12, QSGP_HEADER_SIZE);
	put_u64(builder->data + 16, correlation_id);
	put_u32(builder->data + 24, (uint32_t)payload_size);
	put_u32(builder->data + 28, 0);
	frame->data = builder->data;
	frame->size = builder->size;
	builder->data = NULL;
	builder->size = 0;
	builder->capacity = 0;
	return QSGP_OK;
}

static int add_service_request(struct qsgp_builder *builder,
	const char *service_id)
{
	struct qsgp_builder nested;
	int status;

	status = qsgp_builder_init(&nested, 0);
	if (status != QSGP_OK)
		return status;
	status = qsgp_builder_add_string(&nested, QSGP_TLV_SERVICE_ID,
		service_id, QSGP_MAX_SERVICE_ID);
	if (status == QSGP_OK)
		status = qsgp_builder_add_tlv(builder,
			QSGP_TLV_SERVICE_REQUEST, QSGP_TLV_REQUIRED,
			nested.data, nested.size);
	qsgp_builder_destroy(&nested);
	return status;
}

static int add_service_result(struct qsgp_builder *builder,
	const struct qsgp_service_result *result)
{
	struct qsgp_builder nested;
	int status;

	status = qsgp_builder_init(&nested, 0);
	if (status != QSGP_OK)
		return status;
#define ADD_NESTED(call) do { \
	status = (call); \
	if (status != QSGP_OK) \
		goto out; \
} while (0)
	ADD_NESTED(qsgp_builder_add_string(&nested, QSGP_TLV_SERVICE_ID,
		result->service_id, QSGP_MAX_SERVICE_ID));
	ADD_NESTED(qsgp_builder_add_u32(&nested,
		QSGP_TLV_ADMISSION_DECISION, result->decision));
	ADD_NESTED(qsgp_builder_add_u64(&nested, QSGP_TLV_REASON_CODE,
		result->reason_code));
	if (result->has_reservation_id)
		ADD_NESTED(qsgp_builder_add_u64(&nested,
			QSGP_TLV_RESERVATION_ID, result->reservation_id));
	if (result->has_retry_after_ns)
		ADD_NESTED(qsgp_builder_add_u64(&nested,
			QSGP_TLV_RETRY_AFTER_NS, result->retry_after_ns));
	if (result->has_estimated_start_ns)
		ADD_NESTED(qsgp_builder_add_u64(&nested,
			QSGP_TLV_ESTIMATED_START_NS,
			result->estimated_start_ns));
	if (result->has_estimated_finish_ns)
		ADD_NESTED(qsgp_builder_add_u64(&nested,
			QSGP_TLV_ESTIMATED_FINISH_NS,
			result->estimated_finish_ns));
	if (result->has_runtime_id)
		ADD_NESTED(qsgp_builder_add_string(&nested,
			QSGP_TLV_QPM_RUNTIME_ID, result->qpm_runtime_id,
			QSGP_MAX_SERVICE_ID));
	if (result->has_generation)
		ADD_NESTED(qsgp_builder_add_u64(&nested,
			QSGP_TLV_QPM_GENERATION, result->qpm_generation));
	if (result->has_diagnostic)
		ADD_NESTED(qsgp_builder_add_string(&nested,
			QSGP_TLV_DIAGNOSTIC, result->diagnostic,
			QSGP_MAX_DIAGNOSTIC));
	status = qsgp_builder_add_tlv(builder, QSGP_TLV_SERVICE_RESULT,
		QSGP_TLV_REQUIRED, nested.data, nested.size);
out:
	qsgp_builder_destroy(&nested);
	return status;
#undef ADD_NESTED
}

static int add_release_result(struct qsgp_builder *builder,
	const struct qsgp_release_result *result)
{
	struct qsgp_builder nested;
	int status;

	status = qsgp_builder_init(&nested, 0);
	if (status != QSGP_OK)
		return status;
#define ADD_NESTED(call) do { \
	status = (call); \
	if (status != QSGP_OK) \
		goto out; \
} while (0)
	ADD_NESTED(qsgp_builder_add_string(&nested, QSGP_TLV_SERVICE_ID,
		result->service_id, QSGP_MAX_SERVICE_ID));
	ADD_NESTED(qsgp_builder_add_u64(&nested, QSGP_TLV_RESERVATION_ID,
		result->reservation_id));
	ADD_NESTED(qsgp_builder_add_u32(&nested,
		QSGP_TLV_RESERVATION_STATE, result->state));
	if (result->has_gateway_error)
		ADD_NESTED(qsgp_builder_add_u32(&nested,
			QSGP_TLV_GATEWAY_ERROR_CODE, result->gateway_error));
	if (result->has_diagnostic)
		ADD_NESTED(qsgp_builder_add_string(&nested,
			QSGP_TLV_DIAGNOSTIC, result->diagnostic,
			QSGP_MAX_DIAGNOSTIC));
	status = qsgp_builder_add_tlv(builder, QSGP_TLV_RELEASE_RESULT,
		QSGP_TLV_REQUIRED, nested.data, nested.size);
out:
	qsgp_builder_destroy(&nested);
	return status;
#undef ADD_NESTED
}

static int add_reservation(struct qsgp_builder *builder,
	const struct qsgp_reservation *reservation)
{
	struct qsgp_builder nested;
	int status;

	status = qsgp_builder_init(&nested, 0);
	if (status != QSGP_OK)
		return status;
	status = qsgp_builder_add_string(&nested, QSGP_TLV_SERVICE_ID,
		reservation->service_id, QSGP_MAX_SERVICE_ID);
	if (status == QSGP_OK)
		status = qsgp_builder_add_u64(&nested,
			QSGP_TLV_RESERVATION_ID, reservation->reservation_id);
	if (status == QSGP_OK)
		status = qsgp_builder_add_tlv(builder, QSGP_TLV_RESERVATION,
			QSGP_TLV_REQUIRED, nested.data, nested.size);
	qsgp_builder_destroy(&nested);
	return status;
}

#define ADD_FIELD(call) do { \
	status = (call); \
	if (status != QSGP_OK) \
		goto out; \
} while (0)

static int validate_service_set(const struct qsgp_reserve_request *request)
{
	size_t index;
	size_t other;

	for (index = 0; index < request->service_count; index++) {
		size_t length = bounded_strlen(request->service_ids[index],
			QSGP_MAX_SERVICE_ID);

		if (length == 0 || length > QSGP_MAX_SERVICE_ID)
			return QSGP_ERR_INVALID;
		for (other = 0; other < index; other++) {
			if (strcmp(request->service_ids[index],
				request->service_ids[other]) == 0)
				return QSGP_ERR_CONFLICT;
		}
	}
	return QSGP_OK;
}

static int validate_admission_response(
	const struct qsgp_reserve_response *response, bool reserve)
{
	bool found_delayed = false;
	bool found_rejected = false;
	size_t index;
	size_t other;

	for (index = 0; index < response->result_count; index++) {
		const struct qsgp_service_result *result =
			&response->results[index];
		size_t length = bounded_strlen(result->service_id,
			QSGP_MAX_SERVICE_ID);

		if (length == 0 || length > QSGP_MAX_SERVICE_ID ||
		    result->decision < QSGP_ADMISSION_ACCEPTED ||
		    result->decision > QSGP_ADMISSION_REJECTED ||
		    (reserve && result->decision == QSGP_ADMISSION_ACCEPTED &&
		     (!result->has_reservation_id || result->reservation_id == 0)) ||
		    (!reserve && result->has_reservation_id) ||
		    (result->decision != QSGP_ADMISSION_ACCEPTED &&
		     result->has_reservation_id))
			return QSGP_ERR_INVALID;
		for (other = 0; other < index; other++) {
			if (strcmp(result->service_id,
				response->results[other].service_id) == 0)
				return QSGP_ERR_CONFLICT;
		}
		found_delayed |= result->decision == QSGP_ADMISSION_DELAYED;
		found_rejected |= result->decision == QSGP_ADMISSION_REJECTED;
		if (response->decision == QSGP_ADMISSION_ACCEPTED &&
		    result->decision != QSGP_ADMISSION_ACCEPTED)
			return QSGP_ERR_INVALID;
	}
	if (response->decision == QSGP_ADMISSION_REJECTED && !found_rejected)
		return QSGP_ERR_INVALID;
	if (response->decision == QSGP_ADMISSION_DELAYED &&
	    (found_rejected || !found_delayed))
		return QSGP_ERR_INVALID;
	return QSGP_OK;
}

static int encode_admission_request(
	const struct qsgp_reserve_request *request, uint16_t message_type,
	uint64_t correlation_id, struct qsgp_frame *frame)
{
	struct qsgp_builder builder;
	size_t index;
	int status;

	if (request == NULL || frame == NULL || request->request_id == 0 ||
	    request->canonical_job_id == 0 || request->service_count == 0 ||
	    request->service_count > QSGP_MAX_SERVICES)
		return QSGP_ERR_INVALID;
	if (request->has_hetero_job_id != request->has_hetero_component)
		return QSGP_ERR_INVALID;
	status = validate_service_set(request);
	if (status != QSGP_OK)
		return status;
	if (request->has_workload &&
	    (request->workload.kind < QSGP_WORKLOAD_QUANTUM ||
	     request->workload.kind > QSGP_WORKLOAD_HYBRID ||
	     request->workload.walltime_ns == 0 ||
	     request->workload.circuit_count == 0 ||
	     request->workload.max_qubits == 0 ||
	     request->workload.max_depth == 0 ||
	     request->workload.max_shots == 0))
		return QSGP_ERR_INVALID;
	memset(frame, 0, sizeof(*frame));
	status = qsgp_builder_init(&builder, QSGP_HEADER_SIZE);
	if (status != QSGP_OK)
		return status;
	ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_REQUEST_ID,
		request->request_id));
	ADD_FIELD(qsgp_builder_add_string(&builder, QSGP_TLV_CLUSTER_NAME,
		request->cluster_name, QSGP_MAX_CLUSTER_NAME));
	ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_CANONICAL_JOB_ID,
		request->canonical_job_id));
	if (request->has_hetero_job_id)
		ADD_FIELD(qsgp_builder_add_u64(&builder,
			QSGP_TLV_HETERO_JOB_ID, request->hetero_job_id));
	if (request->has_hetero_component)
		ADD_FIELD(qsgp_builder_add_u32(&builder,
			QSGP_TLV_HETERO_COMPONENT, request->hetero_component));
	ADD_FIELD(qsgp_builder_add_u32(&builder, QSGP_TLV_JOB_UID,
		(uint32_t)request->job_uid));
	ADD_FIELD(qsgp_builder_add_u32(&builder, QSGP_TLV_JOB_GID,
		(uint32_t)request->job_gid));
	if (request->has_workload)
		ADD_FIELD(qsgp_builder_add_u32(&builder,
			QSGP_TLV_WORKLOAD_KIND, request->workload.kind));
	if (request->has_workload)
		ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_WALLTIME_NS,
			request->workload.walltime_ns));
	if (request->has_workload)
		ADD_FIELD(qsgp_builder_add_u64(&builder,
			QSGP_TLV_CIRCUIT_COUNT,
			request->workload.circuit_count));
	if (request->has_workload)
		ADD_FIELD(qsgp_builder_add_u32(&builder, QSGP_TLV_MAX_QUBITS,
			request->workload.max_qubits));
	if (request->has_workload)
		ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_MAX_DEPTH,
			request->workload.max_depth));
	if (request->has_workload)
		ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_MAX_SHOTS,
			request->workload.max_shots));
	if (request->has_workload && request->workload.has_max_one_q_gates)
		ADD_FIELD(qsgp_builder_add_u64(&builder,
			QSGP_TLV_MAX_ONE_Q_GATES,
			request->workload.max_one_q_gates));
	if (request->has_workload && request->workload.has_max_two_q_gates)
		ADD_FIELD(qsgp_builder_add_u64(&builder,
			QSGP_TLV_MAX_TWO_Q_GATES,
			request->workload.max_two_q_gates));
	if (request->has_workload && request->workload.has_max_measurements)
		ADD_FIELD(qsgp_builder_add_u64(&builder,
			QSGP_TLV_MAX_MEASUREMENTS,
			request->workload.max_measurements));
	for (index = 0; index < request->service_count; index++)
		ADD_FIELD(add_service_request(&builder,
			request->service_ids[index]));
	status = qsgp_builder_finish(&builder, message_type,
		correlation_id, frame);
out:
	qsgp_builder_destroy(&builder);
	return status;
}

int qsgp_encode_reserve_request(const struct qsgp_reserve_request *request,
	uint64_t correlation_id, struct qsgp_frame *frame)
{
	if (request == NULL || !request->has_workload)
		return QSGP_ERR_INVALID;
	return encode_admission_request(request, QSGP_RESERVE_REQUEST,
		correlation_id, frame);
}

int qsgp_encode_evaluate_request(const struct qsgp_reserve_request *request,
	uint64_t correlation_id, struct qsgp_frame *frame)
{
	if (request == NULL || !request->has_workload)
		return QSGP_ERR_INVALID;
	return encode_admission_request(request, QSGP_EVALUATE_REQUEST,
		correlation_id, frame);
}

static int encode_admission_response(
	const struct qsgp_reserve_response *response, uint16_t message_type,
	bool reserve, uint64_t correlation_id, struct qsgp_frame *frame)
{
	struct qsgp_builder builder;
	size_t index;
	int status;

	if (response == NULL || frame == NULL || response->request_id == 0 ||
	    response->decision < QSGP_ADMISSION_ACCEPTED ||
	    response->decision > QSGP_ADMISSION_REJECTED ||
	    response->result_count == 0 ||
	    response->result_count > QSGP_MAX_SERVICES)
		return QSGP_ERR_INVALID;
	status = validate_admission_response(response, reserve);
	if (status != QSGP_OK)
		return status;
	memset(frame, 0, sizeof(*frame));
	status = qsgp_builder_init(&builder, QSGP_HEADER_SIZE);
	if (status != QSGP_OK)
		return status;
	ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_REQUEST_ID,
		response->request_id));
	ADD_FIELD(qsgp_builder_add_u32(&builder,
		QSGP_TLV_ADMISSION_DECISION, response->decision));
	for (index = 0; index < response->result_count; index++)
		ADD_FIELD(add_service_result(&builder, &response->results[index]));
	status = qsgp_builder_finish(&builder, message_type,
		correlation_id, frame);
out:
	qsgp_builder_destroy(&builder);
	return status;
}

int qsgp_encode_reserve_response(const struct qsgp_reserve_response *response,
	uint64_t correlation_id, struct qsgp_frame *frame)
{
	return encode_admission_response(response, QSGP_RESERVE_RESPONSE, true,
		correlation_id, frame);
}

int qsgp_encode_evaluate_response(
	const struct qsgp_reserve_response *response,
	uint64_t correlation_id, struct qsgp_frame *frame)
{
	return encode_admission_response(response, QSGP_EVALUATE_RESPONSE, false,
		correlation_id, frame);
}

int qsgp_encode_release_request(const struct qsgp_release_request *request,
	uint64_t correlation_id, struct qsgp_frame *frame)
{
	struct qsgp_builder builder;
	int status;

	if (request == NULL || frame == NULL || request->request_id == 0 ||
	    request->canonical_job_id == 0)
		return QSGP_ERR_INVALID;
	memset(frame, 0, sizeof(*frame));
	status = qsgp_builder_init(&builder, QSGP_HEADER_SIZE);
	if (status != QSGP_OK)
		return status;
	ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_REQUEST_ID,
		request->request_id));
	ADD_FIELD(qsgp_builder_add_string(&builder, QSGP_TLV_CLUSTER_NAME,
		request->cluster_name, QSGP_MAX_CLUSTER_NAME));
	ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_CANONICAL_JOB_ID,
		request->canonical_job_id));
	ADD_FIELD(qsgp_builder_add_u32(&builder, QSGP_TLV_RELEASE_REASON,
		request->reason));
	status = qsgp_builder_finish(&builder, QSGP_RELEASE_REQUEST,
		correlation_id, frame);
out:
	qsgp_builder_destroy(&builder);
	return status;
}

int qsgp_encode_release_response(const struct qsgp_release_response *response,
	uint64_t correlation_id, struct qsgp_frame *frame)
{
	struct qsgp_builder builder;
	size_t index;
	int status;

	if (response == NULL || frame == NULL || response->request_id == 0 ||
	    response->result_count > QSGP_MAX_SERVICES)
		return QSGP_ERR_INVALID;
	memset(frame, 0, sizeof(*frame));
	status = qsgp_builder_init(&builder, QSGP_HEADER_SIZE);
	if (status != QSGP_OK)
		return status;
	ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_REQUEST_ID,
		response->request_id));
	for (index = 0; index < response->result_count; index++)
		ADD_FIELD(add_release_result(&builder, &response->results[index]));
	status = qsgp_builder_finish(&builder, QSGP_RELEASE_RESPONSE,
		correlation_id, frame);
out:
	qsgp_builder_destroy(&builder);
	return status;
}

int qsgp_encode_get_reservations_request(
	const struct qsgp_get_reservations_request *request,
	uint64_t correlation_id, struct qsgp_frame *frame)
{
	struct qsgp_builder builder;
	int status;

	if (request == NULL || frame == NULL || request->request_id == 0 ||
	    request->observed_job_id == 0)
		return QSGP_ERR_INVALID;
	memset(frame, 0, sizeof(*frame));
	status = qsgp_builder_init(&builder, QSGP_HEADER_SIZE);
	if (status != QSGP_OK)
		return status;
	ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_REQUEST_ID,
		request->request_id));
	ADD_FIELD(qsgp_builder_add_string(&builder, QSGP_TLV_CLUSTER_NAME,
		request->cluster_name, QSGP_MAX_CLUSTER_NAME));
	ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_OBSERVED_JOB_ID,
		request->observed_job_id));
	ADD_FIELD(qsgp_builder_add_u32(&builder, QSGP_TLV_JOB_UID,
		(uint32_t)request->job_uid));
	ADD_FIELD(qsgp_builder_add_u32(&builder, QSGP_TLV_JOB_GID,
		(uint32_t)request->job_gid));
	status = qsgp_builder_finish(&builder,
		QSGP_GET_RESERVATIONS_REQUEST, correlation_id, frame);
out:
	qsgp_builder_destroy(&builder);
	return status;
}

int qsgp_encode_get_reservations_response(
	const struct qsgp_get_reservations_response *response,
	uint64_t correlation_id, struct qsgp_frame *frame)
{
	struct qsgp_builder builder;
	size_t index;
	size_t other;
	int status;

	if (response == NULL || frame == NULL || response->request_id == 0 ||
	    response->canonical_job_id == 0 || response->reservation_count == 0 ||
	    response->reservation_count > QSGP_MAX_SERVICES)
		return QSGP_ERR_INVALID;
	for (index = 0; index < response->reservation_count; index++) {
		if (response->reservations[index].service_id[0] == '\0' ||
		    response->reservations[index].reservation_id == 0)
			return QSGP_ERR_INVALID;
		for (other = 0; other < index; other++) {
			if (strcmp(response->reservations[index].service_id,
				response->reservations[other].service_id) == 0)
				return QSGP_ERR_CONFLICT;
		}
	}
	memset(frame, 0, sizeof(*frame));
	status = qsgp_builder_init(&builder, QSGP_HEADER_SIZE);
	if (status != QSGP_OK)
		return status;
	ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_REQUEST_ID,
		response->request_id));
	ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_CANONICAL_JOB_ID,
		response->canonical_job_id));
	for (index = 0; index < response->reservation_count; index++)
		ADD_FIELD(add_reservation(&builder,
			&response->reservations[index]));
	status = qsgp_builder_finish(&builder,
		QSGP_GET_RESERVATIONS_RESPONSE, correlation_id, frame);
out:
	qsgp_builder_destroy(&builder);
	return status;
}

int qsgp_encode_error_response(const struct qsgp_error_response *response,
	uint64_t correlation_id, struct qsgp_frame *frame)
{
	struct qsgp_builder builder;
	int status;

	if (response == NULL || frame == NULL || response->error_code == 0)
		return QSGP_ERR_INVALID;
	memset(frame, 0, sizeof(*frame));
	status = qsgp_builder_init(&builder, QSGP_HEADER_SIZE);
	if (status != QSGP_OK)
		return status;
	if (response->has_request_id)
		ADD_FIELD(qsgp_builder_add_u64(&builder, QSGP_TLV_REQUEST_ID,
			response->request_id));
	ADD_FIELD(qsgp_builder_add_u32(&builder,
		QSGP_TLV_GATEWAY_ERROR_CODE, response->error_code));
	if (response->has_diagnostic)
		ADD_FIELD(qsgp_builder_add_string(&builder,
			QSGP_TLV_DIAGNOSTIC, response->diagnostic,
			QSGP_MAX_DIAGNOSTIC));
	status = qsgp_builder_finish(&builder, QSGP_ERROR_RESPONSE,
		correlation_id, frame);
out:
	qsgp_builder_destroy(&builder);
	return status;
}

void qsgp_frame_destroy(struct qsgp_frame *frame)
{
	if (frame == NULL)
		return;
	free(frame->data);
	memset(frame, 0, sizeof(*frame));
}

uint64_t qsgp_hton64(uint64_t value)
{
#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
	return ((uint64_t)htonl((uint32_t)(value >> 32U))) |
	       ((uint64_t)htonl((uint32_t)value) << 32U);
#else
	return value;
#endif
}

uint64_t qsgp_ntoh64(uint64_t value)
{
	return qsgp_hton64(value);
}

#undef ADD_FIELD
