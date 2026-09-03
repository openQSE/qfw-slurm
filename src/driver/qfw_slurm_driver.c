#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <getopt.h>
#include <inttypes.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "qfw_slurm_native.h"

enum driver_command {
	DRIVER_COMMAND_NONE = 0,
	DRIVER_COMMAND_EVALUATE,
	DRIVER_COMMAND_RESERVE,
	DRIVER_COMMAND_RELEASE,
	DRIVER_COMMAND_GET_RESERVATIONS,
	DRIVER_COMMAND_LIFECYCLE,
};

enum driver_exit_status {
	DRIVER_EXIT_OK = 0,
	DRIVER_EXIT_ARGUMENT = 2,
	DRIVER_EXIT_TRANSPORT = 3,
	DRIVER_EXIT_AUTHENTICATION = 4,
	DRIVER_EXIT_GATEWAY = 5,
	DRIVER_EXIT_ADMISSION = 6,
	DRIVER_EXIT_RESPONSE = 7,
	DRIVER_EXIT_RELEASE = 8,
};

struct driver_options {
	enum driver_command command;
	const char *config_path;
	struct qfw_allocation_context allocation;
	struct qfw_quantum_options quantum;
	uint32_t release_reason;
	uint64_t hold_seconds;
	bool has_cluster;
	bool has_job_id;
	bool has_uid;
	bool has_gid;
	bool has_epoch;
	bool has_walltime;
	bool has_hetero_job;
	bool has_hetero_component;
	bool json;
};

static volatile sig_atomic_t interrupted;

static void signal_handler(int signal_number)
{
	interrupted = signal_number;
}

static void usage(FILE *stream)
{
	(void)fprintf(stream,
		"usage: qfw-slurm-driver "
		"<evaluate|reserve|get-reservations|release|lifecycle> [options]\n"
		"  --config PATH                 protected plugin config\n"
		"  --cluster NAME                Slurm cluster name\n"
		"  --job-id ID                   canonical Slurm job ID\n"
		"  --uid UID                     trusted job user ID\n"
		"  --gid GID                     trusted job group ID\n"
		"  --allocation-epoch SECONDS    job submit epoch\n"
		"  --walltime-seconds SECONDS    finite reservation limit\n"
		"  --hetero-job-id ID            heterogeneous component job ID\n"
		"  --hetero-component INDEX      heterogeneous component index\n"
		"  --qpu NAME[,NAME]             configured quantum resources\n"
		"  --workload-kind KIND          quantum or hybrid\n"
		"  --circ-count COUNT            circuit bound\n"
		"  --max-qubits COUNT            qubit bound\n"
		"  --max-depth COUNT             depth bound\n"
		"  --max-shots COUNT             shot bound\n"
		"  --max-one-q-gates COUNT       optional one-qubit gate bound\n"
		"  --max-two-q-gates COUNT       optional two-qubit gate bound\n"
		"  --max-measurements COUNT      optional measurement bound\n"
		"  --release-reason CODE         release reason, default zero\n"
		"  --hold-seconds SECONDS        lifecycle hold before release\n"
		"  --json                        emit JSON Lines\n"
		"  --help                        show this help\n");
}

static int parse_u64(const char *value, uint64_t *output)
{
	const unsigned char *cursor;
	char *end = NULL;
	unsigned long long parsed;

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

static int copy_cluster(struct driver_options *options, const char *value)
{
	if (value == NULL || *value == '\0' ||
	    strlen(value) > QSGP_MAX_CLUSTER_NAME)
		return -1;
	(void)snprintf(options->allocation.cluster_name,
		sizeof(options->allocation.cluster_name), "%s", value);
	options->has_cluster = true;
	return 0;
}

static int set_quantum_option(struct driver_options *options,
	uint32_t field, const char *value)
{
	char error[QFW_PLUGIN_MAX_ERROR + 1U] = {0};

	if (qfw_quantum_options_set(&options->quantum, field, value,
		error, sizeof(error)) == 0)
		return 0;
	(void)fprintf(stderr, "qfw-slurm-driver: %s\n", error);
	return -1;
}

static int command_from_name(const char *name, enum driver_command *command)
{
	if (strcmp(name, "evaluate") == 0)
		*command = DRIVER_COMMAND_EVALUATE;
	else if (strcmp(name, "reserve") == 0)
		*command = DRIVER_COMMAND_RESERVE;
	else if (strcmp(name, "release") == 0)
		*command = DRIVER_COMMAND_RELEASE;
	else if (strcmp(name, "get-reservations") == 0)
		*command = DRIVER_COMMAND_GET_RESERVATIONS;
	else if (strcmp(name, "lifecycle") == 0)
		*command = DRIVER_COMMAND_LIFECYCLE;
	else
		return -1;
	return 0;
}

static int parse_options(int argc, char **argv,
	struct driver_options *options)
{
	enum {
		OPT_CLUSTER = 1000,
		OPT_JOB_ID,
		OPT_UID,
		OPT_GID,
		OPT_EPOCH,
		OPT_WALLTIME,
		OPT_HETERO_JOB,
		OPT_HETERO_COMPONENT,
		OPT_RELEASE_REASON,
		OPT_HOLD_SECONDS,
		OPT_QPU,
		OPT_WORKLOAD_KIND,
		OPT_CIRCUIT_COUNT,
		OPT_MAX_QUBITS,
		OPT_MAX_DEPTH,
		OPT_MAX_SHOTS,
		OPT_MAX_ONE_Q_GATES,
		OPT_MAX_TWO_Q_GATES,
		OPT_MAX_MEASUREMENTS,
	};
	static const struct option long_options[] = {
		{"config", required_argument, NULL, 'c'},
		{"cluster", required_argument, NULL, OPT_CLUSTER},
		{"job-id", required_argument, NULL, OPT_JOB_ID},
		{"uid", required_argument, NULL, OPT_UID},
		{"gid", required_argument, NULL, OPT_GID},
		{"allocation-epoch", required_argument, NULL, OPT_EPOCH},
		{"walltime-seconds", required_argument, NULL, OPT_WALLTIME},
		{"hetero-job-id", required_argument, NULL, OPT_HETERO_JOB},
		{"hetero-component", required_argument, NULL,
		 OPT_HETERO_COMPONENT},
		{"release-reason", required_argument, NULL, OPT_RELEASE_REASON},
		{"hold-seconds", required_argument, NULL, OPT_HOLD_SECONDS},
		{"qpu", required_argument, NULL, OPT_QPU},
		{"workload-kind", required_argument, NULL, OPT_WORKLOAD_KIND},
		{"circ-count", required_argument, NULL, OPT_CIRCUIT_COUNT},
		{"max-qubits", required_argument, NULL, OPT_MAX_QUBITS},
		{"max-depth", required_argument, NULL, OPT_MAX_DEPTH},
		{"max-shots", required_argument, NULL, OPT_MAX_SHOTS},
		{"max-one-q-gates", required_argument, NULL,
		 OPT_MAX_ONE_Q_GATES},
		{"max-two-q-gates", required_argument, NULL,
		 OPT_MAX_TWO_Q_GATES},
		{"max-measurements", required_argument, NULL,
		 OPT_MAX_MEASUREMENTS},
		{"json", no_argument, NULL, 'j'},
		{"help", no_argument, NULL, 'h'},
		{NULL, 0, NULL, 0},
	};
	uint64_t parsed;
	int option;

	memset(options, 0, sizeof(*options));
	qfw_quantum_options_init(&options->quantum);
	if (argc == 2 && strcmp(argv[1], "--help") == 0) {
		usage(stdout);
		return 1;
	}
	if (argc < 2 || command_from_name(argv[1], &options->command) != 0)
		return -1;
	optind = 2;
	while ((option = getopt_long(argc, argv, "c:jh", long_options,
		NULL)) != -1) {
		switch (option) {
		case 'c':
			options->config_path = optarg;
			break;
		case 'j':
			options->json = true;
			break;
		case 'h':
			usage(stdout);
			return 1;
		case OPT_CLUSTER:
			if (copy_cluster(options, optarg) != 0)
				return -1;
			break;
		case OPT_JOB_ID:
			if (parse_u64(optarg, &parsed) != 0 || parsed == 0)
				return -1;
			options->allocation.canonical_job_id = parsed;
			options->has_job_id = true;
			break;
		case OPT_UID:
			if (parse_u64(optarg, &parsed) != 0 ||
			    (uint64_t)(uid_t)parsed != parsed)
				return -1;
			options->allocation.job_uid = (uid_t)parsed;
			options->has_uid = true;
			break;
		case OPT_GID:
			if (parse_u64(optarg, &parsed) != 0 ||
			    (uint64_t)(gid_t)parsed != parsed)
				return -1;
			options->allocation.job_gid = (gid_t)parsed;
			options->has_gid = true;
			break;
		case OPT_EPOCH:
			if (parse_u64(optarg, &options->allocation.allocation_epoch) != 0)
				return -1;
			options->has_epoch = true;
			break;
		case OPT_WALLTIME:
			if (parse_u64(optarg, &parsed) != 0 || parsed == 0 ||
			    parsed > UINT64_MAX / UINT64_C(1000000000))
				return -1;
			options->allocation.walltime_ns =
				parsed * UINT64_C(1000000000);
			options->has_walltime = true;
			break;
		case OPT_HETERO_JOB:
			if (parse_u64(optarg,
				&options->allocation.hetero_job_id) != 0 ||
			    options->allocation.hetero_job_id == 0)
				return -1;
			options->has_hetero_job = true;
			break;
		case OPT_HETERO_COMPONENT:
			if (parse_u32(optarg,
				&options->allocation.hetero_component) != 0)
				return -1;
			options->has_hetero_component = true;
			break;
		case OPT_RELEASE_REASON:
			if (parse_u32(optarg, &options->release_reason) != 0)
				return -1;
			break;
		case OPT_HOLD_SECONDS:
			if (parse_u64(optarg, &options->hold_seconds) != 0)
				return -1;
			break;
		case OPT_QPU:
			if (set_quantum_option(options, QFW_OPTION_QPU,
				optarg) != 0)
				return -1;
			break;
		case OPT_WORKLOAD_KIND:
			if (set_quantum_option(options, QFW_OPTION_WORKLOAD_KIND,
				optarg) != 0)
				return -1;
			break;
		case OPT_CIRCUIT_COUNT:
			if (set_quantum_option(options, QFW_OPTION_CIRCUIT_COUNT,
				optarg) != 0)
				return -1;
			break;
		case OPT_MAX_QUBITS:
			if (set_quantum_option(options, QFW_OPTION_MAX_QUBITS,
				optarg) != 0)
				return -1;
			break;
		case OPT_MAX_DEPTH:
			if (set_quantum_option(options, QFW_OPTION_MAX_DEPTH,
				optarg) != 0)
				return -1;
			break;
		case OPT_MAX_SHOTS:
			if (set_quantum_option(options, QFW_OPTION_MAX_SHOTS,
				optarg) != 0)
				return -1;
			break;
		case OPT_MAX_ONE_Q_GATES:
			if (set_quantum_option(options,
				QFW_OPTION_MAX_ONE_Q_GATES, optarg) != 0)
				return -1;
			break;
		case OPT_MAX_TWO_Q_GATES:
			if (set_quantum_option(options,
				QFW_OPTION_MAX_TWO_Q_GATES, optarg) != 0)
				return -1;
			break;
		case OPT_MAX_MEASUREMENTS:
			if (set_quantum_option(options,
				QFW_OPTION_MAX_MEASUREMENTS, optarg) != 0)
				return -1;
			break;
		default:
			return -1;
		}
	}
	if (optind != argc || options->config_path == NULL ||
	    !options->has_cluster || !options->has_job_id ||
	    !options->has_uid || !options->has_gid ||
	    options->has_hetero_job != options->has_hetero_component)
		return -1;
	if (options->command != DRIVER_COMMAND_GET_RESERVATIONS &&
	    !options->has_epoch)
		return -1;
	options->allocation.has_hetero = options->has_hetero_job;
	if (options->command != DRIVER_COMMAND_RELEASE &&
	    options->command != DRIVER_COMMAND_GET_RESERVATIONS) {
		char error[QFW_PLUGIN_MAX_ERROR + 1U] = {0};

		if (!options->has_walltime || !options->quantum.active ||
		    qfw_quantum_options_validate(&options->quantum, error,
			sizeof(error)) != 0) {
			if (error[0] != '\0')
				(void)fprintf(stderr, "qfw-slurm-driver: %s\n",
					error);
			return -1;
		}
	}
	return 0;
}

static const char *operation_name(uint32_t state)
{
	switch (state) {
	case QFW_OPERATION_ACCEPTED:
		return "accepted";
	case QFW_OPERATION_DELAYED:
		return "delayed";
	case QFW_OPERATION_REJECTED:
		return "rejected";
	case QFW_OPERATION_CLIENT_ERROR:
		return "client-error";
	case QFW_OPERATION_GATEWAY_ERROR:
		return "gateway-error";
	case QFW_OPERATION_RESPONSE_ERROR:
		return "response-error";
	case QFW_OPERATION_RELEASED:
		return "released";
	case QFW_OPERATION_RELEASE_UNRESOLVED:
		return "release-unresolved";
	default:
		return "invalid";
	}
}

static void print_json_string(const char *value)
{
	const unsigned char *cursor;

	(void)putchar('"');
	for (cursor = (const unsigned char *)value; *cursor != '\0'; cursor++) {
		if (*cursor == '"' || *cursor == '\\')
			(void)printf("\\%c", *cursor);
		else if (*cursor < 0x20U)
			(void)printf("\\u%04x", *cursor);
		else
			(void)putchar(*cursor);
	}
	(void)putchar('"');
}

static void print_reserve(const struct driver_options *options,
	const struct qfw_reserve_operation_result *result)
{
	const char *operation = options->command == DRIVER_COMMAND_EVALUATE ?
		"evaluate" : "reserve";
	size_t index;

	if (options->json) {
		(void)printf("{\"schema\":\"qfw-slurm-driver-v1\","
			"\"operation\":\"%s\",\"request_id\":%" PRIu64
			",\"state\":", operation, result->request.request_id);
		print_json_string(operation_name(result->state));
		(void)printf(",\"diagnostic\":");
		print_json_string(result->diagnostic);
		(void)printf(",\"reservations\":%s}\n",
			result->state == QFW_OPERATION_ACCEPTED &&
			options->command != DRIVER_COMMAND_EVALUATE ?
			result->reservations_json : "null");
	} else {
		(void)printf("%s request=%" PRIu64 " state=%s\n", operation,
			result->request.request_id, operation_name(result->state));
		if (result->diagnostic[0] != '\0')
			(void)printf("diagnostic: %s\n", result->diagnostic);
		for (index = 0; index < result->response.result_count; index++) {
			const struct qsgp_service_result *item =
				&result->response.results[index];

			(void)printf("service=%s decision=%u",
				item->service_id, item->decision);
			if (item->has_reservation_id)
				(void)printf(" reservation=%" PRIu64,
					item->reservation_id);
			(void)putchar('\n');
		}
		if (result->state == QFW_OPERATION_ACCEPTED &&
		    options->command != DRIVER_COMMAND_EVALUATE)
			(void)printf("export QFW_RESERVATIONS='%s'\n",
				result->reservations_json);
	}
}

static void print_release(const struct driver_options *options,
	const struct qfw_release_operation_result *result)
{
	size_t index;

	if (options->json) {
		(void)printf("{\"schema\":\"qfw-slurm-driver-v1\","
			"\"operation\":\"release\",\"request_id\":%" PRIu64
			",\"state\":", result->request.request_id);
		print_json_string(operation_name(result->state));
		(void)printf(",\"unresolved_count\":%zu,\"diagnostic\":",
			result->unresolved_count);
		print_json_string(result->diagnostic);
		(void)printf("}\n");
	} else {
		(void)printf("release request=%" PRIu64 " state=%s unresolved=%zu\n",
			result->request.request_id, operation_name(result->state),
			result->unresolved_count);
		if (result->diagnostic[0] != '\0')
			(void)printf("diagnostic: %s\n", result->diagnostic);
		for (index = 0; index < result->response.result_count; index++) {
			const struct qsgp_release_result *item =
				&result->response.results[index];

			(void)printf("service=%s reservation=%" PRIu64
				" state=%u\n", item->service_id,
				item->reservation_id, item->state);
		}
	}
}

static void print_get_reservations(const struct driver_options *options,
	const struct qfw_get_reservations_operation_result *result)
{
	if (options->json) {
		(void)printf("{\"schema\":\"qfw-slurm-driver-v1\","
			"\"operation\":\"get-reservations\",\"request_id\":%"
			PRIu64 ",\"state\":", result->request.request_id);
		print_json_string(operation_name(result->state));
		(void)printf(",\"canonical_job_id\":%" PRIu64
			",\"diagnostic\":", result->response.canonical_job_id);
		print_json_string(result->diagnostic);
		(void)printf(",\"reservations\":%s}\n",
			result->state == QFW_OPERATION_ACCEPTED ?
			result->reservations_json : "null");
	} else {
		(void)printf("get-reservations request=%" PRIu64 " state=%s\n",
			result->request.request_id, operation_name(result->state));
		if (result->diagnostic[0] != '\0')
			(void)printf("diagnostic: %s\n", result->diagnostic);
		if (result->state == QFW_OPERATION_ACCEPTED)
			(void)printf("export QFW_RESERVATIONS='%s'\n",
				result->reservations_json);
	}
}

static int reserve_exit_status(
	const struct qfw_reserve_operation_result *result)
{
	if (result->state == QFW_OPERATION_ACCEPTED)
		return DRIVER_EXIT_OK;
	if (result->state == QFW_OPERATION_GATEWAY_ERROR)
		return DRIVER_EXIT_GATEWAY;
	if (result->state == QFW_OPERATION_DELAYED ||
	    result->state == QFW_OPERATION_REJECTED)
		return DRIVER_EXIT_ADMISSION;
	if (result->state == QFW_OPERATION_RESPONSE_ERROR)
		return DRIVER_EXIT_RESPONSE;
	if (result->state == QFW_OPERATION_CLIENT_ERROR &&
	    result->call_error.source == QFW_GATEWAY_ERROR_AUTHENTICATION)
		return DRIVER_EXIT_AUTHENTICATION;
	if (result->state == QFW_OPERATION_INVALID)
		return DRIVER_EXIT_ARGUMENT;
	return DRIVER_EXIT_TRANSPORT;
}

static int release_exit_status(
	const struct qfw_release_operation_result *result)
{
	if (result->state == QFW_OPERATION_RELEASED)
		return DRIVER_EXIT_OK;
	if (result->state == QFW_OPERATION_GATEWAY_ERROR)
		return DRIVER_EXIT_GATEWAY;
	if (result->state == QFW_OPERATION_RESPONSE_ERROR)
		return DRIVER_EXIT_RESPONSE;
	if (result->state == QFW_OPERATION_CLIENT_ERROR &&
	    result->call_error.source == QFW_GATEWAY_ERROR_AUTHENTICATION)
		return DRIVER_EXIT_AUTHENTICATION;
	if (result->state == QFW_OPERATION_INVALID)
		return DRIVER_EXIT_ARGUMENT;
	if (result->state == QFW_OPERATION_CLIENT_ERROR)
		return DRIVER_EXIT_TRANSPORT;
	return DRIVER_EXIT_RELEASE;
}

static int lookup_exit_status(
	const struct qfw_get_reservations_operation_result *result)
{
	if (result->state == QFW_OPERATION_ACCEPTED)
		return DRIVER_EXIT_OK;
	if (result->state == QFW_OPERATION_GATEWAY_ERROR)
		return DRIVER_EXIT_GATEWAY;
	if (result->state == QFW_OPERATION_RESPONSE_ERROR)
		return DRIVER_EXIT_RESPONSE;
	if (result->state == QFW_OPERATION_CLIENT_ERROR &&
	    result->call_error.source == QFW_GATEWAY_ERROR_AUTHENTICATION)
		return DRIVER_EXIT_AUTHENTICATION;
	if (result->state == QFW_OPERATION_INVALID)
		return DRIVER_EXIT_ARGUMENT;
	return DRIVER_EXIT_TRANSPORT;
}

static void hold_before_release(uint64_t seconds)
{
	struct sigaction action = {0};
	struct timespec remaining;

	action.sa_handler = signal_handler;
	(void)sigemptyset(&action.sa_mask);
	(void)sigaction(SIGINT, &action, NULL);
	(void)sigaction(SIGTERM, &action, NULL);
	remaining.tv_sec = seconds > (uint64_t)INT64_MAX ?
		(time_t)INT64_MAX : (time_t)seconds;
	remaining.tv_nsec = 0;
	while (!interrupted &&
	       nanosleep(&remaining, &remaining) != 0 && errno == EINTR)
		;
}

int main(int argc, char **argv)
{
	struct driver_options options;
	struct qfw_plugin_config config;
	struct qfw_gateway_client client;
	struct qfw_reserve_operation_result reserve_result;
	struct qfw_release_operation_result release_result;
	struct qfw_get_reservations_operation_result lookup_result;
	char error[QFW_PLUGIN_MAX_ERROR + 1U] = {0};
	int status;

	status = parse_options(argc, argv, &options);
	if (status == 1)
		return DRIVER_EXIT_OK;
	if (status != 0) {
		usage(stderr);
		return DRIVER_EXIT_ARGUMENT;
	}
	if (qfw_plugin_config_load(options.config_path, &config, error,
		sizeof(error)) != 0 ||
	    qfw_gateway_client_init(&client, &config, error,
		sizeof(error)) != QFW_GATEWAY_OK) {
		(void)fprintf(stderr, "qfw-slurm-driver: %s\n", error);
		return DRIVER_EXIT_ARGUMENT;
	}
	if (options.command == DRIVER_COMMAND_GET_RESERVATIONS) {
		(void)qfw_get_reservations_operation(&client,
			options.allocation.cluster_name,
			options.allocation.canonical_job_id,
			options.allocation.job_uid, options.allocation.job_gid,
			&lookup_result);
		print_get_reservations(&options, &lookup_result);
		status = lookup_exit_status(&lookup_result);
		qfw_gateway_client_destroy(&client);
		return status;
	}
	if (options.command != DRIVER_COMMAND_RELEASE) {
		if (options.command == DRIVER_COMMAND_EVALUATE)
			(void)qfw_evaluate_operation(&client, &config,
				&options.quantum, &options.allocation,
				&reserve_result);
		else
			(void)qfw_reserve_operation(&client, &config,
				&options.quantum, &options.allocation,
				&reserve_result);
		print_reserve(&options, &reserve_result);
		status = reserve_exit_status(&reserve_result);
		if (status != DRIVER_EXIT_OK ||
		    options.command == DRIVER_COMMAND_RESERVE ||
		    options.command == DRIVER_COMMAND_EVALUATE) {
			qfw_gateway_client_destroy(&client);
			return status;
		}
		hold_before_release(options.hold_seconds);
	}
	(void)qfw_release_operation(&client, &options.allocation,
		options.release_reason, &release_result);
	print_release(&options, &release_result);
	status = release_exit_status(&release_result);
	qfw_gateway_client_destroy(&client);
	if (status == DRIVER_EXIT_OK && interrupted)
		return 128 + interrupted;
	return status;
}
