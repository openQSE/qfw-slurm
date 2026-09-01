#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "qfw_slurm_native.h"

#define DEFAULT_CONFIG_PATH "/etc/qfw-slurm/plugin.conf"

static int parse_u64(const char *value, uint64_t *output)
{
	char *end = NULL;
	unsigned long long parsed;

	if (value == NULL || *value == '\0')
		return -1;
	errno = 0;
	parsed = strtoull(value, &end, 10);
	if (errno != 0 || end == value || *end != '\0' || parsed == 0)
		return -1;
	*output = (uint64_t)parsed;
	return 0;
}

static int copy_cluster_name(const char *value, char *output,
	size_t output_size)
{
	if (value == NULL || *value == '\0' || strlen(value) >= output_size)
		return -1;
	(void)snprintf(output, output_size, "%s", value);
	return 0;
}

static int load_allocation_identity(struct qfw_allocation_context *allocation)
{
	const char *heterogeneous = getenv("SLURM_HET_JOB_ID");
	const char *start_time = getenv("SLURM_JOB_START_TIME");
	uint64_t job_id;

	memset(allocation, 0, sizeof(*allocation));
	if (parse_u64(getenv("SLURM_JOB_ID"), &job_id) != 0 ||
	    copy_cluster_name(getenv("SLURM_CLUSTER_NAME"),
		allocation->cluster_name,
		sizeof(allocation->cluster_name)) != 0)
		return -1;
	allocation->canonical_job_id = job_id;
	if (heterogeneous != NULL && *heterogeneous != '\0' &&
	    parse_u64(heterogeneous, &allocation->canonical_job_id) != 0)
		return -1;
	if (start_time != NULL && *start_time != '\0' &&
	    parse_u64(start_time, &allocation->allocation_epoch) != 0)
		return -1;
	return 0;
}

int main(int argc, char **argv)
{
	const char *config_path = DEFAULT_CONFIG_PATH;
	struct qfw_plugin_config config;
	struct qfw_gateway_client client;
	struct qfw_allocation_context allocation = {0};
	struct qfw_release_operation_result operation;
	char error[QFW_PLUGIN_MAX_ERROR + 1U] = {0};
	size_t index;

	if (argc == 3 && strcmp(argv[1], "--config") == 0)
		config_path = argv[2];
	else if (argc != 1) {
		fprintf(stderr, "usage: %s [--config PATH]\n", argv[0]);
		return 2;
	}
	if (load_allocation_identity(&allocation) != 0) {
		fprintf(stderr,
			"qfw-slurm-epilog: callback identity is invalid\n");
		return 0;
	}
	if (qfw_plugin_config_load(config_path, &config, error,
		sizeof(error)) != 0 ||
	    qfw_gateway_client_init(&client, &config, error,
		sizeof(error)) != QFW_GATEWAY_OK) {
		fprintf(stderr, "qfw-slurm-epilog: %s\n",
			error[0] != '\0' ? error : "cannot configure gateway client");
		goto out;
	}
	if (qfw_release_operation(&client, &allocation, 0, &operation) != 0) {
		fprintf(stderr,
			"qfw-slurm-epilog: cannot execute release operation\n");
		qfw_gateway_client_destroy(&client);
		goto out;
	}
	qfw_gateway_client_destroy(&client);
	if (operation.state != QFW_OPERATION_RELEASED &&
	    operation.state != QFW_OPERATION_RELEASE_UNRESOLVED) {
		fprintf(stderr, "qfw-slurm-epilog: release failed: %s\n",
			operation.diagnostic[0] != '\0' ? operation.diagnostic :
			"unknown release failure");
		goto out;
	}
	for (index = 0; index < operation.response.result_count; index++) {
		const struct qsgp_release_result *item =
			&operation.response.results[index];

		if (item->state == QSGP_RESERVATION_AUTHORIZATION_FAILURE ||
		    item->state == QSGP_RESERVATION_QPM_FAILURE ||
		    item->state == QSGP_RESERVATION_GATEWAY_FAILURE)
			fprintf(stderr,
				"qfw-slurm-epilog: unresolved service=%s "
				"reservation=%" PRIu64 " state=%u error=%u%s%s\n",
				item->service_id, item->reservation_id, item->state,
				item->has_gateway_error ? item->gateway_error : 0,
				item->has_diagnostic ? " diagnostic=" : "",
				item->has_diagnostic ? item->diagnostic : "");
	}
out:
	return 0;
}
