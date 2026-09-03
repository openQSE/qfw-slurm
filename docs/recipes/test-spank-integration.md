# Test the Live Slurm Integration

This recipe validates allocation-time evaluation, final QPM reservation,
application context injection, and allocation teardown. Run `man 7 qfw-slurm`
for the lifecycle and allocator options. Run `man 8 qfw-slurm-bb` for helper
states and recovery behavior.

## Prerequisites

- A site directory service, QPM, and `qfw-slurm-gateway` are ready.
- Slurm loads `spank_quantum.so` through `PlugStackConfig`.
- `JobSubmitPlugins=lua` loads qfw-slurm's `job_submit.lua`.
- `BurstBufferType=burst_buffer/lua` loads qfw-slurm's `burst_buffer.lua`.
- `/etc/qfw-slurm/resources.lua`, `plugin.conf`, and
  `burst-buffer.lua.conf` are root-owned and not writable by users.
- The configured lifecycle state directory is owned by `SlurmUser` with mode
  `0700` and remains local to the controller.
- Compute nodes can reach the gateway endpoint and read their local protected
  `/etc/qfw-slurm/plugin.conf`. They do not mount the gateway journal or
  controller lifecycle state.

`qfw-slurm-burst-buffer.conf(5)`, `qfw-slurm-plugin.conf(5)`, and
`qfw-slurm-gateway.yaml(5)` describe those files.

## Allocate and inspect application context

Submit the workload bounds to `salloc`, before Slurm assigns nodes:

```bash
salloc --partition=normal \
  --nodes=1 \
  --ntasks=1 \
  --time=00:05:00 \
  --qpu=nwqsim \
  --workload-kind=quantum \
  --circ-count=1 \
  --max-qubits=5 \
  --max-depth=20 \
  --max-shots=64
```

Inside the allocation, inspect two application steps:

```bash
srun /bin/sh -c 'printf "%s\n" "${QFW_RESERVATIONS}"'
srun /bin/sh -c 'printf "%s\n" "${QFW_RESERVATIONS}"'
```

Both must print the same canonical tuple set. Neither step creates another
QPM reservation. Confirm that the allocator-only options are rejected by
`srun`:

```bash
if srun --qpu=nwqsim /bin/true; then
  echo "ERROR: srun accepted an allocator-only option" >&2
  exit 1
fi
```

## Run a QFw application with the accepted reservation

```bash
export QFW_SHARED_ROOT=/workspace/qfw-container-base
export QFW_RUN_BASE_DIR="${HOME}/qfw-runs"
mkdir -p "${QFW_RUN_BASE_DIR}"

source /opt/openqse/qfw/bin/qfw-activate \
  --venv /opt/openqse/qfw-venv
qfw-setup --site-config /etc/openqse/qfw/site.yaml

qfw-srun --nodes 1 --ntasks 1 \
  "${QFW_SHARE_DIR}/examples/tests/test_qiskit_simple.py" 5 nwqsim

qfw-teardown
qfw-deactivate
```

The backend uses the reservation tuple injected into the `qfw-srun` step. It
must not call the allocation reserve API again.

## Terminate and verify release

Record `SLURM_JOB_ID`, then leave the allocation:

```bash
job_id="${SLURM_JOB_ID}"
exit
```

On the service host, inspect the gateway journal:

```bash
qfw-slurm-gateway \
  --config /etc/qfw-slurm/gateway.yaml \
  status "${job_id}"
```

The allocation and all reservation rows must be `released`. An unresolved row
remains available for administrator retry:

```bash
qfw-slurm-gateway-launch \
  --config /etc/qfw-slurm/gateway.yaml \
  retry-release "${job_id}"
```

## Recovery checks

If a job remains in `BurstBufferStageIn`, inspect its protected state and the
gateway log. An `evaluation-delayed` state is normal and must have no
`reservations` field. A `reservation-delayed` state must return the assigned
nodes before the next evaluation cycle.

If controller retry state cannot be written after QPMd commits a reservation,
teardown still sends allocation-wide release using the configured cluster and
job ID. The gateway journal is authoritative for both lookup and release.
