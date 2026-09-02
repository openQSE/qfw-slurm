#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <slurm/slurm.h>
#include <slurm/spank.h>

#include "qfw_slurm_native.h"

#define QFW_STATE_PATH_MAX 4096U

SPANK_PLUGIN(spank_quantum, 1)

static struct qfw_quantum_options quantum_options;
static char state_dir[QFW_STATE_PATH_MAX] =
	"/var/lib/qfw-slurm/allocations";
static uid_t state_owner_uid;

static int parse_arguments(int argc, char **argv)
{
	const char prefix[] = "state-dir=";
	const char owner_prefix[] = "state-owner-uid=";
	int index;

	for (index = 0; index < argc; index++) {
		if (strncmp(argv[index], prefix, sizeof(prefix) - 1U) == 0) {
			if (argv[index][sizeof(prefix) - 1U] == '\0' ||
			    strlen(argv[index] + sizeof(prefix) - 1U) >=
			    sizeof(state_dir))
				return SLURM_ERROR;
			(void)snprintf(state_dir, sizeof(state_dir), "%s",
				argv[index] + sizeof(prefix) - 1U);
		} else if (strncmp(argv[index], owner_prefix,
			sizeof(owner_prefix) - 1U) == 0) {
			char *end = NULL;
			unsigned long value;

			errno = 0;
			value = strtoul(argv[index] + sizeof(owner_prefix) - 1U,
				&end, 10);
			if (errno != 0 || end == NULL || *end != '\0' ||
			    value > UINT32_MAX)
				return SLURM_ERROR;
			state_owner_uid = (uid_t)value;
		} else {
			return SLURM_ERROR;
		}
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
	const char prefix[] = "QFW_RESERVATIONS=";
	char cluster[QSGP_MAX_CLUSTER_NAME + 1U];
	char path[QFW_STATE_PATH_MAX];
	char content[QFW_RESERVATIONS_ENV_SIZE + sizeof(prefix) + 1U];
	struct stat metadata;
	uint32_t job_id;
	ssize_t size;
	int descriptor;

	if (spank_get_item(spank, S_JOB_ID, &job_id) != ESPANK_SUCCESS ||
	    cluster_name(cluster, sizeof(cluster)) != 0)
		return SLURM_ERROR;
	if (snprintf(path, sizeof(path), "%s/%s-%u.env", state_dir,
		cluster, job_id) >= (int)sizeof(path))
		return SLURM_ERROR;
	descriptor = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
	if (descriptor < 0)
		return errno == ENOENT ? SLURM_SUCCESS : SLURM_ERROR;
	if (fstat(descriptor, &metadata) != 0 || !S_ISREG(metadata.st_mode) ||
	    metadata.st_uid != state_owner_uid ||
	    (metadata.st_mode & (S_IWGRP | S_IWOTH)) != 0 ||
	    metadata.st_size <= (off_t)(sizeof(prefix) - 1U) ||
	    metadata.st_size >= (off_t)sizeof(content)) {
		(void)close(descriptor);
		return SLURM_ERROR;
	}
	size = read(descriptor, content, (size_t)metadata.st_size);
	(void)close(descriptor);
	if (size != metadata.st_size)
		return SLURM_ERROR;
	content[size] = '\0';
	if (content[size - 1] == '\n')
		content[--size] = '\0';
	if (strncmp(content, prefix, sizeof(prefix) - 1U) != 0 ||
	    strchr(content, '\n') != NULL)
		return SLURM_ERROR;
	if (spank_setenv(spank, "QFW_RESERVATIONS",
		content + sizeof(prefix) - 1U, 1) != ESPANK_SUCCESS)
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
		slurm_error("%s: invalid state-dir argument", plugin_name);
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
