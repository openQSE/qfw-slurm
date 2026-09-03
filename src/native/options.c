#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "qfw_slurm_native.h"

#define QFW_REQUIRED_OPTIONS \
	(QFW_OPTION_QPU | QFW_OPTION_WORKLOAD_KIND | \
	 QFW_OPTION_CIRCUIT_COUNT | QFW_OPTION_MAX_QUBITS | \
	 QFW_OPTION_MAX_DEPTH | QFW_OPTION_MAX_SHOTS)

static void set_error(char *error, size_t error_size, const char *message)
{
	if (error != NULL && error_size != 0)
		(void)snprintf(error, error_size, "%s", message);
}

static int parse_u64(const char *value, uint64_t *output)
{
	char *end = NULL;
	unsigned long long parsed;
	const unsigned char *cursor;

	if (value == NULL || *value == '\0')
		return -1;
	for (cursor = (const unsigned char *)value; *cursor != '\0'; cursor++) {
		if (*cursor < '0' || *cursor > '9')
			return -1;
	}
	errno = 0;
	parsed = strtoull(value, &end, 10);
	if (errno != 0 || end == value || *end != '\0')
		return -1;
	*output = (uint64_t)parsed;
	return 0;
}

static int parse_u32(const char *value, uint32_t *output)
{
	uint64_t parsed;

	if (parse_u64(value, &parsed) != 0 || parsed > UINT32_MAX)
		return -1;
	*output = (uint32_t)parsed;
	return 0;
}

static int parse_qpu_names(const char *value,
	struct qfw_quantum_options *output)
{
	const char *cursor = value;

	if (value == NULL || *value == '\0')
		return -1;
	while (*cursor != '\0') {
		const char *separator = strchr(cursor, ',');
		size_t length = separator == NULL ? strlen(cursor) :
			(size_t)(separator - cursor);
		size_t index;

		if (length == 0 || length > QFW_PLUGIN_MAX_RESOURCE_NAME ||
		    output->qpu_count >= QSGP_MAX_SERVICES)
			return -1;
		for (index = 0; index < length; index++) {
			unsigned char character = (unsigned char)cursor[index];

			if (!((character >= 'a' && character <= 'z') ||
			      (character >= 'A' && character <= 'Z') ||
			      (character >= '0' && character <= '9') ||
			      character == '-' || character == '_' ||
			      character == '.'))
				return -1;
		}
		memcpy(output->qpu_names[output->qpu_count], cursor, length);
		output->qpu_names[output->qpu_count][length] = '\0';
		for (index = 0; index < output->qpu_count; index++) {
			if (strcmp(output->qpu_names[index],
				output->qpu_names[output->qpu_count]) == 0)
				return -1;
		}
		output->qpu_count++;
		if (separator == NULL)
			break;
		cursor = separator + 1U;
	}
	output->active = true;
	return 0;
}

void qfw_quantum_options_init(struct qfw_quantum_options *options)
{
	if (options != NULL)
		memset(options, 0, sizeof(*options));
}

static bool qpu_values_equal(const struct qfw_quantum_options *left,
	const struct qfw_quantum_options *right)
{
	size_t index;

	if (left->qpu_count != right->qpu_count)
		return false;
	for (index = 0; index < left->qpu_count; index++) {
		if (strcmp(left->qpu_names[index], right->qpu_names[index]) != 0)
			return false;
	}
	return true;
}

static int set_qpu(struct qfw_quantum_options *options, const char *value)
{
	struct qfw_quantum_options parsed;

	qfw_quantum_options_init(&parsed);
	if (parse_qpu_names(value, &parsed) != 0)
		return -1;
	if ((options->present_fields & QFW_OPTION_QPU) != 0)
		return qpu_values_equal(options, &parsed) ? 0 : -2;
	options->qpu_count = parsed.qpu_count;
	options->active = true;
	memcpy(options->qpu_names, parsed.qpu_names,
		sizeof(options->qpu_names));
	return 0;
}

static int set_workload_kind(struct qfw_quantum_options *options,
	const char *value)
{
	uint32_t parsed;

	if (strcmp(value, "quantum") == 0)
		parsed = QSGP_WORKLOAD_QUANTUM;
	else if (strcmp(value, "hybrid") == 0)
		parsed = QSGP_WORKLOAD_HYBRID;
	else
		return -1;
	if ((options->present_fields & QFW_OPTION_WORKLOAD_KIND) != 0)
		return options->workload.kind == parsed ? 0 : -2;
	options->workload.kind = parsed;
	return 0;
}

static int set_u64_field(struct qfw_quantum_options *options, uint32_t field,
	const char *value, uint64_t *destination, bool *presence)
{
	uint64_t parsed;

	if (parse_u64(value, &parsed) != 0)
		return -1;
	if ((options->present_fields & field) != 0)
		return *destination == parsed ? 0 : -2;
	*destination = parsed;
	if (presence != NULL)
		*presence = true;
	return 0;
}

int qfw_quantum_options_set(struct qfw_quantum_options *options,
	uint32_t field, const char *value, char *error, size_t error_size)
{
	int status;

	if (options == NULL || value == NULL) {
		set_error(error, error_size, "option value is required");
		return -1;
	}
	switch (field) {
	case QFW_OPTION_QPU:
		status = set_qpu(options, value);
		break;
	case QFW_OPTION_WORKLOAD_KIND:
		status = set_workload_kind(options, value);
		break;
	case QFW_OPTION_CIRCUIT_COUNT:
		status = set_u64_field(options, field, value,
			&options->workload.circuit_count, NULL);
		break;
	case QFW_OPTION_MAX_QUBITS: {
		uint32_t parsed;

		if (parse_u32(value, &parsed) != 0)
			status = -1;
		else if ((options->present_fields & field) != 0)
			status = options->workload.max_qubits == parsed ? 0 : -2;
		else {
			options->workload.max_qubits = parsed;
			status = 0;
		}
		break;
	}
	case QFW_OPTION_MAX_DEPTH:
		status = set_u64_field(options, field, value,
			&options->workload.max_depth, NULL);
		break;
	case QFW_OPTION_MAX_SHOTS:
		status = set_u64_field(options, field, value,
			&options->workload.max_shots, NULL);
		break;
	case QFW_OPTION_MAX_ONE_Q_GATES:
		status = set_u64_field(options, field, value,
			&options->workload.max_one_q_gates,
			&options->workload.has_max_one_q_gates);
		break;
	case QFW_OPTION_MAX_TWO_Q_GATES:
		status = set_u64_field(options, field, value,
			&options->workload.max_two_q_gates,
			&options->workload.has_max_two_q_gates);
		break;
	case QFW_OPTION_MAX_MEASUREMENTS:
		status = set_u64_field(options, field, value,
			&options->workload.max_measurements,
			&options->workload.has_max_measurements);
		break;
	default:
		set_error(error, error_size, "unknown quantum option");
		return -1;
	}
	if (status == -2) {
		set_error(error, error_size, "conflicting duplicate quantum option");
		return -1;
	}
	if (status != 0) {
		set_error(error, error_size, "invalid quantum option value");
		return -1;
	}
	options->active = true;
	options->present_fields |= field;
	return 0;
}

int qfw_quantum_options_validate(const struct qfw_quantum_options *options,
	char *error, size_t error_size)
{
	if (options == NULL)
		return -1;
	if (!options->active)
		return 0;
	if ((options->present_fields & QFW_REQUIRED_OPTIONS) !=
	    QFW_REQUIRED_OPTIONS) {
		set_error(error, error_size,
			"managed QPU requests require workload and circuit bounds");
		return -1;
	}
	if (options->qpu_count == 0 || options->workload.circuit_count == 0 ||
	    options->workload.max_qubits == 0 ||
	    options->workload.max_depth == 0 ||
	    options->workload.max_shots == 0) {
		set_error(error, error_size,
			"required quantum workload bounds must be nonzero");
		return -1;
	}
	return 0;
}
