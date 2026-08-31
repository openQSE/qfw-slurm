#define _POSIX_C_SOURCE 200809L

#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <slurm/slurm.h>
#include <slurm/spank.h>

#include "qfw_slurm_native.h"

SPANK_PLUGIN(spank_quantum, 1)

#define RESERVATIONS_ENV_SIZE 16384U

static struct qfw_plugin_config plugin_config;
static struct qfw_quantum_options quantum_options;
static bool launch_failed;
static char deferred_error[QFW_PLUGIN_MAX_ERROR + 1U];

static void remember_error(const char *message)
{
	launch_failed = true;
	(void)snprintf(deferred_error, sizeof(deferred_error), "%s", message);
	slurm_error("%s: %s", plugin_name, deferred_error);
}

static int option_callback(int value, const char *argument, int remote)
{
	char error[QFW_PLUGIN_MAX_ERROR + 1U] = {0};

	if (qfw_quantum_options_set(&quantum_options, (uint32_t)value,
		argument, error, sizeof(error)) == 0)
		return SLURM_SUCCESS;
	slurm_error("%s: %s", plugin_name, error);
	if (remote) {
		remember_error(error);
		return SLURM_SUCCESS;
	}
	return SLURM_ERROR;
}

static struct spank_option plugin_options[] = {
	{"qpu", "resource[,resource]", "QPU resources to reserve", 1,
	 QFW_OPTION_QPU, option_callback},
	{"workload-kind", "quantum|hybrid", "Quantum workload kind", 1,
	 QFW_OPTION_WORKLOAD_KIND, option_callback},
	{"circ-count", "count", "Maximum circuit count", 1,
	 QFW_OPTION_CIRCUIT_COUNT, option_callback},
	{"max-qubits", "count", "Maximum qubits per circuit", 1,
	 QFW_OPTION_MAX_QUBITS, option_callback},
	{"max-depth", "count", "Maximum circuit depth", 1,
	 QFW_OPTION_MAX_DEPTH, option_callback},
	{"max-shots", "count", "Maximum shots per circuit", 1,
	 QFW_OPTION_MAX_SHOTS, option_callback},
	{"max-one-q-gates", "count", "Maximum one-qubit gates", 1,
	 QFW_OPTION_MAX_ONE_Q_GATES, option_callback},
	{"max-two-q-gates", "count", "Maximum two-qubit gates", 1,
	 QFW_OPTION_MAX_TWO_Q_GATES, option_callback},
	{"max-measurements", "count", "Maximum measurements", 1,
	 QFW_OPTION_MAX_MEASUREMENTS, option_callback},
	SPANK_OPTIONS_TABLE_END
};

static const char *config_path_from_args(int argc, char **argv)
{
	const char prefix[] = "config=";
	int index;

	for (index = 0; index < argc; index++) {
		if (strncmp(argv[index], prefix, sizeof(prefix) - 1U) == 0 &&
		    argv[index][sizeof(prefix) - 1U] != '\0')
			return argv[index] + sizeof(prefix) - 1U;
	}
	return NULL;
}

static int register_options(spank_t spank)
{
	struct spank_option *option;

	for (option = plugin_options; option->name != NULL; option++) {
		if (spank_option_register(spank, option) != ESPANK_SUCCESS)
			return SLURM_ERROR;
	}
	return SLURM_SUCCESS;
}

int slurm_spank_init(spank_t spank, int argc, char **argv)
{
	const char *config_path;
	char error[QFW_PLUGIN_MAX_ERROR + 1U] = {0};
	enum spank_context context = spank_context();

	qfw_quantum_options_init(&quantum_options);
	launch_failed = false;
	deferred_error[0] = '\0';
	config_path = config_path_from_args(argc, argv);
	if (config_path == NULL) {
		slurm_error("%s: config=<path> is required", plugin_name);
		return SLURM_ERROR;
	}
	if (qfw_plugin_config_load(config_path, &plugin_config, error,
		sizeof(error)) != 0) {
		slurm_error("%s: %s", plugin_name, error);
		return SLURM_ERROR;
	}
	if (context == S_CTX_ALLOCATOR || context == S_CTX_LOCAL ||
	    context == S_CTX_REMOTE)
		return register_options(spank);
	return SLURM_SUCCESS;
}

static int load_cluster_name(char *cluster_name, size_t cluster_name_size)
{
	slurm_conf_t *configuration = NULL;
	int status;

	status = slurm_load_ctl_conf((time_t)0, &configuration);
	if (status != SLURM_SUCCESS || configuration == NULL ||
	    configuration->cluster_name == NULL ||
	    configuration->cluster_name[0] == '\0') {
		if (configuration != NULL)
			slurm_free_ctl_conf(configuration);
		return -1;
	}
	if (strlen(configuration->cluster_name) >= cluster_name_size) {
		slurm_free_ctl_conf(configuration);
		return -1;
	}
	(void)snprintf(cluster_name, cluster_name_size, "%s",
		configuration->cluster_name);
	slurm_free_ctl_conf(configuration);
	return 0;
}

static job_info_t *find_job(job_info_msg_t *message, uint32_t job_id)
{
	uint32_t index;

	for (index = 0; index < message->record_count; index++) {
		if (message->job_array[index].job_id == job_id)
			return &message->job_array[index];
	}
	return message->record_count == 1 ? &message->job_array[0] : NULL;
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

static int build_request(spank_t spank,
	struct qsgp_reserve_request *request, char *error, size_t error_size)
{
	job_info_msg_t *message = NULL;
	job_info_t *job;
	char cluster_name[QSGP_MAX_CLUSTER_NAME + 1U];
	uint32_t job_id;
	uid_t job_uid;
	gid_t job_gid;
	uint64_t canonical_job_id;
	uint64_t walltime_ns;
	uint64_t allocation_epoch;
	bool has_hetero;
	int result = -1;

	if (spank_get_item(spank, S_JOB_ID, &job_id) != ESPANK_SUCCESS ||
	    spank_get_item(spank, S_JOB_UID, &job_uid) != ESPANK_SUCCESS ||
	    spank_get_item(spank, S_JOB_GID, &job_gid) != ESPANK_SUCCESS) {
		(void)snprintf(error, error_size,
			"cannot read trusted Slurm job identity");
		return -1;
	}
	if (slurm_load_job(&message, job_id, SHOW_ALL) != SLURM_SUCCESS ||
	    message == NULL) {
		(void)snprintf(error, error_size,
			"cannot load authoritative Slurm job record");
		goto out;
	}
	job = find_job(message, job_id);
	if (job == NULL || job->user_id != (uint32_t)job_uid ||
	    job->group_id != (uint32_t)job_gid) {
		(void)snprintf(error, error_size,
			"Slurm job identity does not match plugin context");
		goto out;
	}
	if (job->time_limit == 0 || job->time_limit == INFINITE ||
	    job->time_limit == NO_VAL ||
	    job->time_limit > UINT64_MAX / UINT64_C(60000000000)) {
		(void)snprintf(error, error_size,
			"managed QPU requests require a finite Slurm time limit");
		goto out;
	}
	if (load_cluster_name(cluster_name, sizeof(cluster_name)) != 0) {
		(void)snprintf(error, error_size,
			"cannot read the Slurm cluster name");
		goto out;
	}
	has_hetero = job->het_job_id != 0 && job->het_job_id != NO_VAL;
	canonical_job_id = has_hetero ? job->het_job_id : job_id;
	allocation_epoch = job->submit_time > 0 ?
		(uint64_t)job->submit_time : 0;
	walltime_ns = (uint64_t)job->time_limit * UINT64_C(60000000000);
	result = qfw_build_reserve_request(&plugin_config, &quantum_options,
		cluster_name, canonical_job_id, allocation_epoch, job_uid, job_gid,
		has_hetero, has_hetero ? job_id : 0,
		has_hetero ? job->het_job_offset : 0, walltime_ns,
		request, error, error_size);
out:
	if (message != NULL)
		slurm_free_job_info_msg(message);
	return result;
}

static void remember_gateway_failure(int status,
	const struct qfw_gateway_result *result)
{
	const char *message = qsgp_status_string(status);

	if (status == QSGP_OK && result->is_error) {
		if (result->error.has_diagnostic)
			message = result->error.diagnostic;
		else
			message = "gateway rejected the reservation request";
	}
	remember_error(message);
}

int slurm_spank_init_post_opt(spank_t spank, int argc, char **argv)
{
	struct qsgp_reserve_request request;
	struct qfw_gateway_result result;
	char reservations[RESERVATIONS_ENV_SIZE];
	char error[QFW_PLUGIN_MAX_ERROR + 1U] = {0};
	int status;

	(void)argc;
	(void)argv;
	if (!quantum_options.active)
		return SLURM_SUCCESS;
	if (!spank_remote(spank)) {
		if (qfw_quantum_options_validate(&quantum_options, error,
			sizeof(error)) != 0) {
			slurm_error("%s: %s", plugin_name, error);
			return SLURM_ERROR;
		}
		return SLURM_SUCCESS;
	}
	if (launch_failed)
		return SLURM_SUCCESS;
	if (qfw_quantum_options_validate(&quantum_options, error,
		sizeof(error)) != 0 ||
	    build_request(spank, &request, error, sizeof(error)) != 0) {
		remember_error(error);
		return SLURM_SUCCESS;
	}
	status = qfw_gateway_reserve(&plugin_config, &request,
		correlation_id(request.request_id), &result);
	if (status != QSGP_OK || result.is_error) {
		remember_gateway_failure(status, &result);
		return SLURM_SUCCESS;
	}
	if (result.reserve.request_id != request.request_id ||
	    result.reserve.decision != QSGP_ADMISSION_ACCEPTED) {
		const struct qsgp_service_result *decisive =
			result.reserve.result_count != 0 ?
			&result.reserve.results[0] : NULL;

		if (decisive != NULL && decisive->has_diagnostic)
			remember_error(decisive->diagnostic);
		else
			remember_error("QPM reservation was not accepted");
		return SLURM_SUCCESS;
	}
	if (qfw_reservations_json(&result.reserve, reservations,
		sizeof(reservations)) != 0 ||
	    spank_setenv(spank, "QFW_RESERVATIONS", reservations, 1) !=
		ESPANK_SUCCESS) {
		remember_error("cannot export QFW_RESERVATIONS");
		return SLURM_SUCCESS;
	}
	slurm_info("%s: request=%" PRIu64 " services=%zu decision=accepted",
		plugin_name, request.request_id, result.reserve.result_count);
	return SLURM_SUCCESS;
}

int slurm_spank_task_init(spank_t spank, int argc, char **argv)
{
	(void)spank;
	(void)argc;
	(void)argv;
	if (!launch_failed)
		return SLURM_SUCCESS;
	slurm_error("%s: task launch denied: %s", plugin_name, deferred_error);
	return SLURM_ERROR;
}

int slurm_spank_exit(spank_t spank, int argc, char **argv)
{
	(void)spank;
	(void)argc;
	(void)argv;
	qfw_quantum_options_init(&quantum_options);
	launch_failed = false;
	deferred_error[0] = '\0';
	return SLURM_SUCCESS;
}
