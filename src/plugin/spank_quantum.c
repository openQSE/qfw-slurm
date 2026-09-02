#include <stdint.h>
#include <stdio.h>

#include <slurm/slurm.h>
#include <slurm/spank.h>

#include "qfw_slurm_native.h"

SPANK_PLUGIN(spank_quantum, 1)

static struct qfw_quantum_options quantum_options;

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
	(void)argv;
	qfw_quantum_options_init(&quantum_options);
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
