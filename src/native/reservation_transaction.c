#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "qfw_slurm_native.h"

static void set_error(char *error, size_t error_size, const char *message)
{
	if (error != NULL && error_size != 0)
		(void)snprintf(error, error_size, "%s", message);
}
static uint64_t fnv1a_byte(uint64_t hash, uint8_t value)
{
	return (hash ^ value) * UINT64_C(1099511628211);
}

static uint64_t fnv1a_u64(uint64_t hash, uint64_t value)
{
	unsigned int shift;

	for (shift = 0; shift < 64U; shift += 8U)
		hash = fnv1a_byte(hash, (uint8_t)(value >> shift));
	return hash;
}

uint64_t qfw_request_id(const char *cluster_name, uint64_t job_id,
	uint64_t allocation_epoch, uint32_t operation)
{
	uint64_t hash = UINT64_C(1469598103934665603);
	const unsigned char *cursor;

	if (cluster_name == NULL)
		return 0;
	for (cursor = (const unsigned char *)cluster_name; *cursor != '\0';
	     cursor++)
		hash = fnv1a_byte(hash, *cursor);
	hash = fnv1a_byte(hash, 0);
	hash = fnv1a_u64(hash, job_id);
	hash = fnv1a_u64(hash, allocation_epoch);
	hash = fnv1a_u64(hash, operation);
	return hash == 0 ? UINT64_MAX : hash;
}

static int compare_service_ids(const void *left, const void *right)
{
	return strcmp(left, right);
}

int qfw_build_reserve_request(const struct qfw_plugin_config *config,
	const struct qfw_quantum_options *options, const char *cluster_name,
	uint64_t canonical_job_id, uint64_t allocation_epoch, uid_t job_uid,
	gid_t job_gid, bool has_hetero, uint64_t hetero_job_id,
	uint32_t hetero_component, uint64_t walltime_ns,
	struct qsgp_reserve_request *request, char *error,
	size_t error_size)
{
	size_t index;

	if (config == NULL || options == NULL || cluster_name == NULL ||
	    *cluster_name == '\0' || canonical_job_id == 0 ||
	    walltime_ns == 0 || request == NULL) {
		set_error(error, error_size, "reservation metadata is invalid");
		return -1;
	}
	if (qfw_quantum_options_validate(options, error, error_size) != 0)
		return -1;
	memset(request, 0, sizeof(*request));
	if (strlen(cluster_name) > QSGP_MAX_CLUSTER_NAME) {
		set_error(error, error_size, "cluster name is too long");
		return -1;
	}
	(void)snprintf(request->cluster_name, sizeof(request->cluster_name),
		"%s", cluster_name);
	request->canonical_job_id = canonical_job_id;
	request->job_uid = job_uid;
	request->job_gid = job_gid;
	request->has_workload = !qfw_quantum_options_is_retrieval(options);
	if (request->has_workload) {
		request->workload = options->workload;
		request->workload.walltime_ns = walltime_ns;
	}
	request->request_id = qfw_request_id(cluster_name, canonical_job_id,
		allocation_epoch, QSGP_RESERVE_REQUEST);
	if (has_hetero) {
		request->has_hetero_job_id = true;
		request->hetero_job_id = hetero_job_id;
		request->has_hetero_component = true;
		request->hetero_component = hetero_component;
	}
	for (index = 0; index < options->qpu_count; index++) {
		const char *service_id = qfw_plugin_config_service_id(config,
			options->qpu_names[index]);

		if (service_id == NULL) {
			if (error != NULL && error_size != 0)
				(void)snprintf(error, error_size,
					"QPU resource is not configured: %s",
					options->qpu_names[index]);
			return -1;
		}
		(void)snprintf(request->service_ids[request->service_count],
			sizeof(request->service_ids[request->service_count]),
			"%s", service_id);
		request->service_count++;
	}
	qsort(request->service_ids, request->service_count,
		sizeof(request->service_ids[0]), compare_service_ids);
	return 0;
}
