#ifndef QSGP_TYPES_H
#define QSGP_TYPES_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#define QSGP_MAGIC "QSGP"
#define QSGP_VERSION_MAJOR 1U
#define QSGP_VERSION_MINOR 0U
#define QSGP_HEADER_SIZE 32U

#define QSGP_MAX_FRAME_SIZE (256U * 1024U)
#define QSGP_MAX_CREDENTIAL_SIZE (512U * 1024U)
#define QSGP_MAX_CLUSTER_NAME 128U
#define QSGP_MAX_SERVICE_ID 256U
#define QSGP_MAX_DIAGNOSTIC 4096U
#define QSGP_MAX_SERVICES 32U

#define QSGP_TLV_REQUIRED 0x0001U

enum qsgp_status {
	QSGP_OK = 0,
	QSGP_ERR_INVALID = -1,
	QSGP_ERR_NOMEM = -2,
	QSGP_ERR_BOUNDS = -3,
	QSGP_ERR_VERSION = -4,
	QSGP_ERR_IO = -5,
	QSGP_ERR_AUTH = -6,
	QSGP_ERR_TIMEOUT = -7,
	QSGP_ERR_CONFLICT = -8,
};

enum qsgp_message_type {
	QSGP_RESERVE_REQUEST = 0x0001,
	QSGP_RELEASE_REQUEST = 0x0002,
	QSGP_EVALUATE_REQUEST = 0x0003,
	QSGP_GET_RESERVATIONS_REQUEST = 0x0004,
	QSGP_RESERVE_RESPONSE = 0x8001,
	QSGP_RELEASE_RESPONSE = 0x8002,
	QSGP_EVALUATE_RESPONSE = 0x8003,
	QSGP_GET_RESERVATIONS_RESPONSE = 0x8004,
	QSGP_ERROR_RESPONSE = 0x8fff,
};

enum qsgp_tlv_type {
	QSGP_TLV_CLUSTER_NAME = 0x0001,
	QSGP_TLV_CANONICAL_JOB_ID = 0x0002,
	QSGP_TLV_HETERO_JOB_ID = 0x0003,
	QSGP_TLV_HETERO_COMPONENT = 0x0004,
	QSGP_TLV_JOB_UID = 0x0005,
	QSGP_TLV_JOB_GID = 0x0006,
	QSGP_TLV_SERVICE_ID = 0x0007,
	QSGP_TLV_WORKLOAD_KIND = 0x0008,
	QSGP_TLV_WALLTIME_NS = 0x0009,
	QSGP_TLV_CIRCUIT_COUNT = 0x000a,
	QSGP_TLV_MAX_QUBITS = 0x000b,
	QSGP_TLV_MAX_DEPTH = 0x000c,
	QSGP_TLV_MAX_SHOTS = 0x000d,
	QSGP_TLV_MAX_ONE_Q_GATES = 0x000e,
	QSGP_TLV_MAX_TWO_Q_GATES = 0x000f,
	QSGP_TLV_MAX_MEASUREMENTS = 0x0010,
	QSGP_TLV_RESERVATION_ID = 0x0011,
	QSGP_TLV_RELEASE_REASON = 0x0012,
	QSGP_TLV_ADMISSION_DECISION = 0x0013,
	QSGP_TLV_REASON_CODE = 0x0014,
	QSGP_TLV_RETRY_AFTER_NS = 0x0015,
	QSGP_TLV_DIAGNOSTIC = 0x0016,
	QSGP_TLV_QPM_RUNTIME_ID = 0x0017,
	QSGP_TLV_QPM_GENERATION = 0x0018,
	QSGP_TLV_RESERVATION_STATE = 0x0019,
	QSGP_TLV_REQUEST_ID = 0x001a,
	QSGP_TLV_SERVICE_REQUEST = 0x001b,
	QSGP_TLV_ESTIMATED_START_NS = 0x001c,
	QSGP_TLV_ESTIMATED_FINISH_NS = 0x001d,
	QSGP_TLV_GATEWAY_ERROR_CODE = 0x001e,
	QSGP_TLV_SERVICE_RESULT = 0x001f,
	QSGP_TLV_RELEASE_RESULT = 0x0020,
	QSGP_TLV_OBSERVED_JOB_ID = 0x0021,
	QSGP_TLV_RESERVATION = 0x0022,
};

enum qsgp_workload_kind {
	QSGP_WORKLOAD_QUANTUM = 1,
	QSGP_WORKLOAD_HYBRID = 2,
};

enum qsgp_admission_decision {
	QSGP_ADMISSION_ACCEPTED = 1,
	QSGP_ADMISSION_DELAYED = 2,
	QSGP_ADMISSION_REJECTED = 3,
};

enum qsgp_reservation_state {
	QSGP_RESERVATION_RELEASED = 1,
	QSGP_RESERVATION_ALREADY_TERMINAL = 2,
	QSGP_RESERVATION_NOT_FOUND = 3,
	QSGP_RESERVATION_STALE_RUNTIME = 4,
	QSGP_RESERVATION_AUTHORIZATION_FAILURE = 5,
	QSGP_RESERVATION_QPM_FAILURE = 6,
	QSGP_RESERVATION_GATEWAY_FAILURE = 7,
};

enum qsgp_gateway_error {
	QSGP_GATEWAY_ERROR_INVALID_REQUEST = 1,
	QSGP_GATEWAY_ERROR_UNAUTHORIZED = 2,
	QSGP_GATEWAY_ERROR_DIRECTORY = 3,
	QSGP_GATEWAY_ERROR_QPM = 4,
	QSGP_GATEWAY_ERROR_TIMEOUT = 5,
	QSGP_GATEWAY_ERROR_INTERNAL = 6,
	QSGP_GATEWAY_ERROR_REQUEST_CONFLICT = 7,
	QSGP_GATEWAY_ERROR_UNSUPPORTED_VERSION = 8,
	QSGP_GATEWAY_ERROR_ALLOCATION_NOT_FOUND = 9,
	QSGP_GATEWAY_ERROR_ALLOCATION_NOT_ACCEPTED = 10,
	QSGP_GATEWAY_ERROR_ALLOCATION_RELEASED = 11,
};

struct qsgp_frame {
	uint8_t *data;
	size_t size;
};

struct qsgp_header {
	uint16_t major_version;
	uint16_t minor_version;
	uint16_t message_type;
	uint16_t flags;
	uint64_t correlation_id;
	uint32_t payload_size;
};

struct qsgp_workload {
	uint32_t kind;
	uint64_t walltime_ns;
	uint64_t circuit_count;
	uint32_t max_qubits;
	uint64_t max_depth;
	uint64_t max_shots;
	uint64_t max_one_q_gates;
	uint64_t max_two_q_gates;
	uint64_t max_measurements;
	bool has_max_one_q_gates;
	bool has_max_two_q_gates;
	bool has_max_measurements;
};

struct qsgp_reserve_request {
	uint64_t request_id;
	char cluster_name[QSGP_MAX_CLUSTER_NAME + 1U];
	uint64_t canonical_job_id;
	uint64_t hetero_job_id;
	uint32_t hetero_component;
	bool has_hetero_job_id;
	bool has_hetero_component;
	uid_t job_uid;
	gid_t job_gid;
	struct qsgp_workload workload;
	bool has_workload;
	size_t service_count;
	char service_ids[QSGP_MAX_SERVICES][QSGP_MAX_SERVICE_ID + 1U];
};

struct qsgp_service_result {
	char service_id[QSGP_MAX_SERVICE_ID + 1U];
	uint32_t decision;
	uint64_t reservation_id;
	uint64_t reason_code;
	uint64_t retry_after_ns;
	uint64_t estimated_start_ns;
	uint64_t estimated_finish_ns;
	char qpm_runtime_id[QSGP_MAX_SERVICE_ID + 1U];
	uint64_t qpm_generation;
	char diagnostic[QSGP_MAX_DIAGNOSTIC + 1U];
	bool has_reservation_id;
	bool has_retry_after_ns;
	bool has_estimated_start_ns;
	bool has_estimated_finish_ns;
	bool has_runtime_id;
	bool has_generation;
	bool has_diagnostic;
};

struct qsgp_reserve_response {
	uint64_t request_id;
	uint32_t decision;
	size_t result_count;
	struct qsgp_service_result results[QSGP_MAX_SERVICES];
};

struct qsgp_release_request {
	uint64_t request_id;
	char cluster_name[QSGP_MAX_CLUSTER_NAME + 1U];
	uint64_t canonical_job_id;
	uint32_t reason;
};

struct qsgp_release_result {
	char service_id[QSGP_MAX_SERVICE_ID + 1U];
	uint64_t reservation_id;
	uint32_t state;
	uint32_t gateway_error;
	char diagnostic[QSGP_MAX_DIAGNOSTIC + 1U];
	bool has_gateway_error;
	bool has_diagnostic;
};

struct qsgp_release_response {
	uint64_t request_id;
	size_t result_count;
	struct qsgp_release_result results[QSGP_MAX_SERVICES];
};

struct qsgp_get_reservations_request {
	uint64_t request_id;
	char cluster_name[QSGP_MAX_CLUSTER_NAME + 1U];
	uint64_t observed_job_id;
	uid_t job_uid;
	gid_t job_gid;
};

struct qsgp_reservation {
	char service_id[QSGP_MAX_SERVICE_ID + 1U];
	uint64_t reservation_id;
};

struct qsgp_get_reservations_response {
	uint64_t request_id;
	uint64_t canonical_job_id;
	size_t reservation_count;
	struct qsgp_reservation reservations[QSGP_MAX_SERVICES];
};

struct qsgp_error_response {
	uint64_t request_id;
	uint32_t error_code;
	char diagnostic[QSGP_MAX_DIAGNOSTIC + 1U];
	bool has_request_id;
	bool has_diagnostic;
};

struct qsgp_peer_identity {
	uid_t uid;
	gid_t gid;
	pid_t pid;
	unsigned int encode_time;
};

#endif
