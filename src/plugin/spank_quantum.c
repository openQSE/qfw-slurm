#define _POSIX_C_SOURCE 200809L

#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <slurm/slurm.h>
#include <slurm/spank.h>

#include "qfw_slurm_native.h"

#define QFW_CONFIG_PATH_MAX 4096U
#define QFW_QPU_OPTION_ENV "_SLURM_SPANK_OPTION_spank_quantum_qpu"

SPANK_PLUGIN(spank_quantum, 1)

static struct qfw_quantum_options quantum_options;
static char config_path[QFW_CONFIG_PATH_MAX] = "/etc/qfw-slurm/plugin.conf";

static int parse_arguments(int argc, char **argv)
{
	const char prefix[] = "config=";
	int index;

	for (index = 0; index < argc; index++) {
		if (strncmp(argv[index], prefix, sizeof(prefix) - 1U) != 0 ||
		    argv[index][sizeof(prefix) - 1U] == '\0' ||
		    strlen(argv[index] + sizeof(prefix) - 1U) >=
		    sizeof(config_path))
			return SLURM_ERROR;
		(void)snprintf(config_path, sizeof(config_path), "%s",
			argv[index] + sizeof(prefix) - 1U);
	}
	return SLURM_SUCCESS;
}

static int cluster_name(char *output, size_t output_size)
{
	slurm_conf_t *configuration = NULL;
	int status;

	status = slurm_load_ctl_conf((time_t)0, &configuration);
	if (status != SLURM_SUCCESS || configuration == NULL ||
	    configuration->cluster_name == NULL ||
	    strlen(configuration->cluster_name) >= output_size) {
		if (configuration != NULL)
			slurm_free_ctl_conf(configuration);
		return -1;
	}
	(void)snprintf(output, output_size, "%s", configuration->cluster_name);
	slurm_free_ctl_conf(configuration);
	return 0;
}

static int inject_reservation_context(spank_t spank)
{
	struct qfw_get_reservations_operation_result result;
	struct qfw_plugin_config config;
	struct qfw_gateway_client client;
	char cluster[QSGP_MAX_CLUSTER_NAME + 1U];
	char option_value[QFW_PLUGIN_MAX_RESOURCE_NAME + 1U];
	char error[QFW_PLUGIN_MAX_ERROR + 1U] = {0};
	uint32_t job_id;
	uid_t job_uid;
	gid_t job_gid;
	int status;

	status = spank_getenv(spank, QFW_QPU_OPTION_ENV, option_value,
		sizeof(option_value));
	if (status == ESPANK_ENV_NOEXIST)
		return SLURM_SUCCESS;
	if (status != ESPANK_SUCCESS || option_value[0] == '\0')
		return SLURM_ERROR;
	if (spank_get_item(spank, S_JOB_ID, &job_id) != ESPANK_SUCCESS ||
	    spank_get_item(spank, S_JOB_UID, &job_uid) != ESPANK_SUCCESS ||
	    spank_get_item(spank, S_JOB_GID, &job_gid) != ESPANK_SUCCESS ||
	    cluster_name(cluster, sizeof(cluster)) != 0)
		return SLURM_ERROR;
	if (qfw_plugin_config_load(config_path, &config, error,
		sizeof(error)) != 0 ||
	    qfw_gateway_client_init(&client, &config, error,
		sizeof(error)) != QFW_GATEWAY_OK) {
		slurm_error("%s: %s", plugin_name, error);
		return SLURM_ERROR;
	}
	status = qfw_get_reservations_operation(&client, cluster, job_id,
		job_uid, job_gid, &result);
	qfw_gateway_client_destroy(&client);
	if (status != 0 || result.state != QFW_OPERATION_ACCEPTED) {
		slurm_error("%s: reservation lookup failed: %s", plugin_name,
			result.diagnostic[0] == '\0' ? "unknown error" :
			result.diagnostic);
		return SLURM_ERROR;
	}
	if (spank_setenv(spank, "QFW_RESERVATIONS",
		result.reservations_json, 1) != ESPANK_SUCCESS)
		return SLURM_ERROR;
	return SLURM_SUCCESS;
}

static int option_callback(int value, const char *argument, int remote)
{
	char error[QFW_PLUGIN_MAX_ERROR + 1U] = {0};

	(void)remote;
	if (qfw_quantum_options_set(&quantum_options, (uint32_t)value,
		argument, error, sizeof(error)) == 0)
		return SLURM_SUCCESS;
	slurm_error("%s: %s", plugin_name, error);
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
	(void)argc;
	qfw_quantum_options_init(&quantum_options);
	if (parse_arguments(argc, argv) != SLURM_SUCCESS) {
		slurm_error("%s: invalid config argument", plugin_name);
		return SLURM_ERROR;
	}
	if (spank_context() == S_CTX_REMOTE)
		return inject_reservation_context(spank);
	if (spank_context() != S_CTX_ALLOCATOR)
		return SLURM_SUCCESS;
	return register_options(spank);
}

int slurm_spank_init_post_opt(spank_t spank, int argc, char **argv)
{
	char error[QFW_PLUGIN_MAX_ERROR + 1U] = {0};

	(void)spank;
	(void)argc;
	(void)argv;
	if (spank_context() != S_CTX_ALLOCATOR || !quantum_options.active)
		return SLURM_SUCCESS;
	if (qfw_quantum_options_validate(&quantum_options, error,
		sizeof(error)) == 0)
		return SLURM_SUCCESS;
	slurm_error("%s: %s", plugin_name, error);
	return SLURM_ERROR;
}

int slurm_spank_exit(spank_t spank, int argc, char **argv)
{
	(void)spank;
	(void)argc;
	(void)argv;
	qfw_quantum_options_init(&quantum_options);
	return SLURM_SUCCESS;
}
