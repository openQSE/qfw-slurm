# Test the Driver Against a Live Gateway and QPM

This recipe validates the production gateway path without involving a SPANK
callback. The gateway must be running through `defwp`, and the requested QPM
must already be registered with the configured DEFw directory service.

Run `man 1 qfw-slurm-driver`, `man 8 qfw-slurm-gateway-launch`, and
`man 5 qfw-slurm-plugin.conf` before using a hardware-backed service.

## Prerequisites

- MUNGE is running on the driver and gateway hosts.
- `/etc/qfw-slurm/plugin.conf` maps `nwqsim` to the exact registered service ID.
- `/etc/qfw-slurm/gateway.yaml` selects the active cluster and QFw site file.
- The directory service, NWQSim QPM, and gateway report ready.

Check the live dependencies from an activated QFw environment:

```bash
source /opt/openqse/qfw/bin/qfw-activate \
  --venv /opt/openqse/qfw-venv

qfw-slurm-gateway-launch \
  --config /etc/qfw-slurm/gateway.yaml \
  check --service nwqsim-site
```

The command must return `"status": "ready"` and the current QPM runtime ID and
generation.

## Run one driver lifecycle

Request an interactive allocation, then run these commands inside it:

```bash
salloc --nodes=1 --ntasks=1 --time=00:10:00

cluster_name="$(
  scontrol show config |
    awk -F= '/^ClusterName/ {gsub(/[[:space:]]/, "", $2); print $2}'
)"

qfw-slurm-driver lifecycle \
  --config /etc/qfw-slurm/plugin.conf \
  --cluster "${cluster_name}" \
  --job-id "${SLURM_JOB_ID}" \
  --uid "$(id -u)" \
  --gid "$(id -g)" \
  --allocation-epoch "$(date +%s)" \
  --walltime-seconds 600 \
  --qpu nwqsim \
  --workload-kind quantum \
  --circ-count 1 \
  --max-qubits 5 \
  --max-depth 20 \
  --max-shots 64
```

Successful output contains an accepted reserve result, a canonical
`QFW_RESERVATIONS` export, and a released result. Print and copy the job ID,
then leave the allocation:

```bash
printf 'job ID: %s\n' "${SLURM_JOB_ID}"
exit
```

## Verify the journal

```bash
job_id=12345  # Replace with the copied job ID.
qfw-slurm-gateway \
  --config /etc/qfw-slurm/gateway.yaml \
  status "${job_id}"
```

The allocation and every reservation must be `released`. Run
`man 8 qfw-slurm-gateway` for status fields and release recovery.
