#ifndef QFW_SLURM_NATIVE_H
#define QFW_SLURM_NATIVE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#include "qsgp/qsgp_protocol.h"

#define QFW_PLUGIN_MAX_HOST 256U
#define QFW_PLUGIN_MAX_PORT 16U
#define QFW_PLUGIN_MAX_RESOURCE_NAME 128U
#define QFW_PLUGIN_MAX_RESOURCES QSGP_MAX_SERVICES
#define QFW_PLUGIN_MAX_ERROR QSGP_MAX_DIAGNOSTIC
#define QFW_RESERVATIONS_ENV_SIZE 16384U

enum qfw_gateway_status {
	QFW_GATEWAY_OK = 0,
	QFW_GATEWAY_ERR_REMOTE = -100,
};

enum qfw_gateway_error_source {
	QFW_GATEWAY_ERROR_NONE = 0,
	QFW_GATEWAY_ERROR_LOCAL,
	QFW_GATEWAY_ERROR_TRANSPORT,
	QFW_GATEWAY_ERROR_AUTHENTICATION,
	QFW_GATEWAY_ERROR_PROTOCOL,
	QFW_GATEWAY_ERROR_REMOTE,
};

enum qfw_operation_state {
	QFW_OPERATION_INVALID = 0,
	QFW_OPERATION_ACCEPTED,
	QFW_OPERATION_DELAYED,
	QFW_OPERATION_REJECTED,
	QFW_OPERATION_CLIENT_ERROR,
	QFW_OPERATION_GATEWAY_ERROR,
	QFW_OPERATION_RESPONSE_ERROR,
	QFW_OPERATION_RELEASED,
	QFW_OPERATION_RELEASE_UNRESOLVED,
};

enum qfw_option_field {
	QFW_OPTION_QPU = 1U << 0,
	QFW_OPTION_WORKLOAD_KIND = 1U << 1,
	QFW_OPTION_CIRCUIT_COUNT = 1U << 2,
	QFW_OPTION_MAX_QUBITS = 1U << 3,
	QFW_OPTION_MAX_DEPTH = 1U << 4,
	QFW_OPTION_MAX_SHOTS = 1U << 5,
	QFW_OPTION_MAX_ONE_Q_GATES = 1U << 6,
	QFW_OPTION_MAX_TWO_Q_GATES = 1U << 7,
	QFW_OPTION_MAX_MEASUREMENTS = 1U << 8,
};

struct qfw_resource_mapping {
	char name[QFW_PLUGIN_MAX_RESOURCE_NAME + 1U];
	char service_id[QSGP_MAX_SERVICE_ID + 1U];
};

struct qfw_plugin_config {
	char gateway_host[QFW_PLUGIN_MAX_HOST + 1U];
	char gateway_port[QFW_PLUGIN_MAX_PORT + 1U];
	uint32_t connect_timeout_ms;
	uint32_t request_timeout_ms;
	size_t max_credential_bytes;
	uid_t expected_munge_uid;
	size_t resource_count;
	struct qfw_resource_mapping resources[QFW_PLUGIN_MAX_RESOURCES];
};

struct qfw_gateway_client {
	char host[QFW_PLUGIN_MAX_HOST + 1U];
	char port[QFW_PLUGIN_MAX_PORT + 1U];
	uint32_t connect_timeout_ms;
	uint32_t request_timeout_ms;
	size_t max_credential_bytes;
	uid_t expected_munge_uid;
};

struct qfw_gateway_call_error {
	uint32_t source;
	int qsgp_status;
	struct qsgp_error_response remote;
};

struct qfw_allocation_context {
	char cluster_name[QSGP_MAX_CLUSTER_NAME + 1U];
	uint64_t canonical_job_id;
	uint64_t allocation_epoch;
	uid_t job_uid;
	gid_t job_gid;
	uint64_t walltime_ns;
	uint64_t hetero_job_id;
	uint32_t hetero_component;
	bool has_hetero;
};

struct qfw_reserve_operation_result {
	uint32_t state;
	struct qsgp_reserve_request request;
	struct qsgp_reserve_response response;
	struct qfw_gateway_call_error call_error;
	char reservations_json[QFW_RESERVATIONS_ENV_SIZE];
	char diagnostic[QFW_PLUGIN_MAX_ERROR + 1U];
};

struct qfw_release_operation_result {
	uint32_t state;
	struct qsgp_release_request request;
	struct qsgp_release_response response;
	struct qfw_gateway_call_error call_error;
	size_t unresolved_count;
	char diagnostic[QFW_PLUGIN_MAX_ERROR + 1U];
};

struct qfw_quantum_options {
	bool active;
	char qpu_names[QSGP_MAX_SERVICES]
		[QFW_PLUGIN_MAX_RESOURCE_NAME + 1U];
	size_t qpu_count;
	struct qsgp_workload workload;
	uint32_t present_fields;
};

void qfw_plugin_config_init(struct qfw_plugin_config *config);
int qfw_plugin_config_load(const char *path,
	struct qfw_plugin_config *config, char *error, size_t error_size);
const char *qfw_plugin_config_service_id(
	const struct qfw_plugin_config *config, const char *resource_name);

void qfw_quantum_options_init(struct qfw_quantum_options *options);
int qfw_quantum_options_set(struct qfw_quantum_options *options,
	uint32_t field, const char *value, char *error, size_t error_size);
int qfw_quantum_options_validate(const struct qfw_quantum_options *options,
	char *error, size_t error_size);
bool qfw_quantum_options_is_retrieval(
	const struct qfw_quantum_options *options);

uint64_t qfw_request_id(const char *cluster_name, uint64_t job_id,
	uint64_t allocation_epoch, uint32_t operation);
int qfw_build_reserve_request(const struct qfw_plugin_config *config,
	const struct qfw_quantum_options *options, const char *cluster_name,
	uint64_t canonical_job_id, uint64_t allocation_epoch, uid_t job_uid,
	gid_t job_gid, bool has_hetero, uint64_t hetero_job_id,
	uint32_t hetero_component, uint64_t walltime_ns,
	struct qsgp_reserve_request *request, char *error,
	size_t error_size);
int qfw_reservations_json(const struct qsgp_reserve_response *response,
	char *output, size_t output_size);

int qfw_gateway_client_init(struct qfw_gateway_client *client,
	const struct qfw_plugin_config *config, char *error, size_t error_size);
void qfw_gateway_client_destroy(struct qfw_gateway_client *client);
const char *qfw_gateway_call_error_message(
	const struct qfw_gateway_call_error *error);

int qfw_gateway_reserve(const struct qfw_gateway_client *client,
	const struct qsgp_reserve_request *request,
	struct qsgp_reserve_response *response,
	struct qfw_gateway_call_error *error);
int qfw_gateway_release(const struct qfw_gateway_client *client,
	const struct qsgp_release_request *request,
	struct qsgp_release_response *response,
	struct qfw_gateway_call_error *error);

int qfw_reserve_operation(const struct qfw_gateway_client *client,
	const struct qfw_plugin_config *config,
	const struct qfw_quantum_options *options,
	const struct qfw_allocation_context *allocation,
	struct qfw_reserve_operation_result *result);
int qfw_release_operation(const struct qfw_gateway_client *client,
	const struct qfw_allocation_context *allocation, uint32_t reason,
	struct qfw_release_operation_result *result);

int qfw_reserve_response_process(
	const struct qsgp_reserve_request *request,
	const struct qsgp_reserve_response *response,
	struct qfw_reserve_operation_result *result);
int qfw_release_response_process(
	const struct qsgp_release_request *request,
	const struct qsgp_release_response *response,
	struct qfw_release_operation_result *result);

#endif
