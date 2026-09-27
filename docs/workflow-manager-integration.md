---
type: markdown
---

# Workflow Manager Integration Guide

This guide is for workflow managers that submit Slurm allocations requiring a
QFw-managed quantum reservation. The integration point is the Slurm allocation
request: emit the qfw-slurm options on `sbatch` or `salloc`, let qfw-slurm own
the reservation lifecycle, and launch the workflow task on the allocated node.

## Integration Contract

At submission time, add the qfw-slurm SPANK options to the `sbatch` or `salloc`
command. qfw-slurm validates those options, maps public QPU names to stable QPM
service IDs, and owns the QPM reservation lifecycle. The workflow manager's
responsibility is to request the allocation and launch the workflow task under
that allocation. For QFw applications, that task normally runs through the QFw
runtime launcher: `qfw-setup`, `qfw-srun`, and `qfw-teardown`.

## Submission Options

Supply these options to `sbatch` or `salloc`. They are not `srun` options.

| Option | Required | Value | Meaning |
| --- | --- | --- | --- |
| `--qpu` | Yes | `name[,name...]` | Public QPU resource names configured by the site. |
| `--workload-kind` | Yes | `quantum` or `hybrid` | Whether the allocation is quantum-only or hybrid classical/quantum. |
| `--circ-count` | Yes | Positive integer | Maximum number of circuits expected for the allocation. |
| `--max-qubits` | Yes | Positive integer | Maximum qubits per circuit. |
| `--max-depth` | Yes | Positive integer | Maximum circuit depth. |
| `--max-shots` | Yes | Positive integer | Maximum shots per circuit. |
| `--max-one-q-gates` | No | Positive integer | Optional maximum one-qubit gates. |
| `--max-two-q-gates` | No | Positive integer | Optional maximum two-qubit gates. |
| `--max-measurements` | No | Positive integer | Optional maximum measurements. |

QPU names may contain letters, digits, `_`, `-`, and `.`. Multiple QPUs are a
comma-separated list with no spaces. The site maps these public names to exact
QPM service IDs in qfw-slurm configuration.

The current local cluster configuration accepts these `--qpu` values:

| `--qpu` value | QPM service ID | Notes |
| --- | --- | --- |
| `nwqsim` | `nwqsim` | NWQSim simulator QPM. |
| `ornl-iqm-20q` | `iqm-ornl-20q` | ORNL IQM 20-qubit QPM. |
| `ornl-shim-20q` | `shim-ornl-20q` | QRMI/QDMI shim for the ORNL IQM 20-qubit device. |
| `fake-iqm-20q` | `fake-iqm` | Fake IQM QPM for testing. |

These names come from `QFw-SLURM-Cluster/config/qfw-slurm/resources.lua` and
`QFw-SLURM-Cluster/config/qfw-slurm/plugin.conf`. That cluster currently
allows all four names on the `normal` partition.

All numeric bounds are upper bounds for the allocation. They must be positive
base-10 integers. `--max-qubits` is carried as a 32-bit unsigned value by the
native protocol; the other numeric fields are carried as unsigned 64-bit values.

## Minimal Examples

Batch submission:

```bash
sbatch \
  --partition=normal \
  --qpu=nwqsim \
  --workload-kind=quantum \
  --circ-count=2 \
  --max-qubits=5 \
  --max-depth=120 \
  --max-shots=1024 \
  workflow-job.sh
```

Interactive allocation:

```bash
salloc \
  --partition=normal \
  --qpu=nwqsim,ornl-iqm-20q \
  --workload-kind=hybrid \
  --circ-count=4 \
  --max-qubits=20 \
  --max-depth=500 \
  --max-shots=4096
```

Inside the allocation, launch application steps normally:

```bash
qfw-setup
qfw-srun ./run-quantum-task
qfw-teardown
```

Do not repeat the qfw-slurm options on `srun` or `qfw-srun`.

## Implementation Pattern

Build qfw-slurm support as an optional submission adapter:

1. Collect workflow-level quantum requirements from the user or workflow
   description.
2. Validate that all required bounds are present and positive.
3. Add the qfw-slurm options to the allocation command only when a quantum
   reservation is requested.
4. Submit with `sbatch` or `salloc`.
5. Launch the workflow task under the resulting Slurm allocation. For QFw
   applications, use `qfw-setup`, `qfw-srun`, and `qfw-teardown` inside that
   allocation.

Track the submitted job with the workflow manager's normal Slurm integration,
such as `squeue`, `sacct`, `scontrol`, or the Slurm API.

For an `sbatch` script, prefer `#SBATCH` directives when the values are static.
The generated script should launch the real workflow application directly
through the QFw runtime lifecycle; do not call QFw example harnesses such as
`qfw_run_all.sh`.

```bash
#!/usr/bin/env bash
#SBATCH --partition=normal
#SBATCH --qpu=nwqsim
#SBATCH --workload-kind=quantum
#SBATCH --circ-count=2
#SBATCH --max-qubits=5
#SBATCH --max-depth=120
#SBATCH --max-shots=1024
#SBATCH --job-name=qfw-workflow
#SBATCH --time=45
#SBATCH --nodes=1
#SBATCH --ntasks=1

set -euo pipefail

QFW_PREFIX="${QFW_PREFIX:-/opt/openqse/qfw}"
QFW_VENV="${QFW_VENV:-/opt/openqse/qfw-venv}"
export QFW_SHARED_ROOT="${QFW_SHARED_ROOT:-/workspace/qfw-container-base}"
export QFW_RUN_BASE_DIR="${QFW_RUN_BASE_DIR:-${HOME}/qfw-runs}"
export DEFW_LOG_LEVEL="${DEFW_LOG_LEVEL:-error}"
export DEFW_PY_LOGLEVEL="${DEFW_PY_LOGLEVEL:-critical}"

mkdir -p "${QFW_RUN_BASE_DIR}"

source "${QFW_PREFIX}/bin/qfw-activate" --venv "${QFW_VENV}"

qfw_runtime_ready=0
cleanup() {
    local rc=$?
    trap - EXIT
    if [[ "${qfw_runtime_ready}" == "1" ]]; then
        qfw-teardown || true
    fi
    qfw-deactivate || true
    exit "${rc}"
}
trap cleanup EXIT

setup_args=()
if [[ -n "${QFW_SITE_CONFIG:-}" ]]; then
    setup_args+=(--site-config "${QFW_SITE_CONFIG}")
fi

qfw-setup "${setup_args[@]}"
qfw_runtime_ready=1

qfw-srun run_workflow.py --shots 16
```

After generating the batch file, submit it with the workflow manager's normal
Slurm submission path, for example `sbatch --parsable workflow-job.sbatch`.

## Error Handling

Common submission-time errors include:

| Symptom | Likely cause | Adapter response |
| --- | --- | --- |
| `managed QPU requests require workload and circuit bounds` | At least one qfw-slurm option was supplied, but a required option is missing. | Emit the complete required option set or omit qfw-slurm options entirely. |
| `required quantum workload bounds must be nonzero` | A required numeric bound is zero. | Reject the workflow description before submission. |
| `invalid quantum option value` | A value has the wrong syntax or exceeds a supported integer range. | Validate locally and surface the invalid field to the user. |
| `unknown QPU resource` | `--qpu` names a resource not configured at the site. | Ask the site for supported public QPU names. |
| `QPU resource is not allowed in partition ...` | The selected partition does not permit the named QPU. | Select an allowed partition/QPU combination. |
| Job remains pending | QPM evaluation returned delayed or Slurm is waiting for resources. | Keep tracking the Slurm job with `squeue`, `sacct`, `scontrol`, or the Slurm API; do not create a separate QPM reservation. |
| Job is cancelled before execution | QPM admission rejected the request or qfw-slurm could not complete the reservation. | Report the Slurm job reason/diagnostic to the workflow user. |

## Adapter Checklist

- Emit qfw-slurm options only on `sbatch` or `salloc`.
- Require all six required options when any qfw-slurm option is present.
- Use public QPU names in `--qpu`; do not submit private QPM service IDs unless
  the site documents them as public names.
- Keep resource bounds conservative and explicit. qfw-slurm reserves against the
  maximums you submit.
- Let Slurm and qfw-slurm own retry, rejection, and release behavior.
- Do not depend on the internal `#QFW` burst-buffer directive format.
