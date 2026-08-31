#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <slurm/slurm.h>

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

static int cluster_name(char *output, size_t output_size)
{
	slurm_conf_t *configuration = NULL;
	int status = slurm_load_ctl_conf((time_t)0, &configuration);

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

static job_info_t *find_job(job_info_msg_t *message, uint32_t job_id)
{
	uint32_t index;

	for (index = 0; index < message->record_count; index++) {
		if (message->job_array[index].job_id == job_id)
			return &message->job_array[index];
	}
	return message->record_count == 1 ? &message->job_array[0] : NULL;
}

static int canonical_job_id(uint64_t supplied, uint64_t *canonical)
{
	job_info_msg_t *message = NULL;
	job_info_t *job;
	int result = -1;

	if (supplied > UINT32_MAX ||
	    slurm_load_job(&message, (uint32_t)supplied, SHOW_ALL) !=
		SLURM_SUCCESS || message == NULL)
		goto out;
	job = find_job(message, (uint32_t)supplied);
	if (job == NULL)
		goto out;
	*canonical = job->het_job_id != 0 && job->het_job_id != NO_VAL ?
		job->het_job_id : supplied;
	result = 0;
out:
	if (message != NULL)
		slurm_free_job_info_msg(message);
	return result;
}

static uint64_t correlation_id(uint64_t request_id)
{
	struct timespec now;
	uint64_t value = request_id ^ (uint64_t)getpid();

	if (clock_gettime(CLOCK_MONOTONIC, &now) == 0)
		value ^= (uint64_t)now.tv_nsec << 16U;
	return value == 0 ? UINT64_MAX : value;
}

int main(int argc, char **argv)
{
	const char *config_path = DEFAULT_CONFIG_PATH;
	const char *job_value = getenv("SLURM_JOB_ID");
	struct qfw_plugin_config config;
	struct qsgp_release_request request;
	struct qfw_gateway_result result;
	char cluster[QSGP_MAX_CLUSTER_NAME + 1U];
	char error[QFW_PLUGIN_MAX_ERROR + 1U] = {0};
	uint64_t supplied_job_id;
	uint64_t job_id;
	int status;
	size_t index;

	if (argc == 3 && strcmp(argv[1], "--config") == 0)
		config_path = argv[2];
	else if (argc != 1) {
		fprintf(stderr, "usage: %s [--config PATH]\n", argv[0]);
		return 2;
	}
	if (parse_u64(job_value, &supplied_job_id) != 0) {
		fprintf(stderr, "qfw-slurm-epilog: SLURM_JOB_ID is invalid\n");
		return 0;
	}
	if (canonical_job_id(supplied_job_id, &job_id) != 0) {
		fprintf(stderr,
			"qfw-slurm-epilog: cannot load authoritative Slurm job\n");
		return 0;
	}
	if (qfw_plugin_config_load(config_path, &config, error,
		sizeof(error)) != 0 || cluster_name(cluster, sizeof(cluster)) != 0) {
		fprintf(stderr, "qfw-slurm-epilog: %s\n",
			error[0] != '\0' ? error : "cannot read cluster name");
		return 0;
	}
	memset(&request, 0, sizeof(request));
	request.canonical_job_id = job_id;
	request.reason = 0;
	(void)snprintf(request.cluster_name, sizeof(request.cluster_name),
		"%s", cluster);
	request.request_id = qfw_request_id(cluster, job_id, 0,
		QSGP_RELEASE_REQUEST);
	status = qfw_gateway_release(&config, &request,
		correlation_id(request.request_id), &result);
	if (status != QSGP_OK) {
		fprintf(stderr, "qfw-slurm-epilog: release transport failed: %s\n",
			qsgp_status_string(status));
		return 0;
	}
	if (result.is_error) {
		fprintf(stderr, "qfw-slurm-epilog: gateway release failed: %s\n",
			result.error.has_diagnostic ? result.error.diagnostic :
			"gateway error");
		return 0;
	}
	for (index = 0; index < result.release.result_count; index++) {
		const struct qsgp_release_result *item =
			&result.release.results[index];

		if (item->state >= QSGP_RESERVATION_QPM_FAILURE)
			fprintf(stderr,
				"qfw-slurm-epilog: unresolved service=%s "
				"reservation=%" PRIu64 " state=%u\n",
				item->service_id, item->reservation_id, item->state);
	}
	return 0;
}
