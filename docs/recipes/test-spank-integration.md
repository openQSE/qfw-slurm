# Test the Live SPANK Integration

This recipe validates the boundary that the standalone driver cannot cover.
Slurm loads `spank_quantum.so`, the remote callback reserves the selected QPM,
the task receives `QFW_RESERVATIONS`, and `EpilogSlurmctld` releases the
reservation after allocation termination.

Run `man 7 qfw-slurm` for the SPANK options and lifecycle. Run
`man 8 qfw-slurm-epilog` for controller cleanup behavior.

## Prerequisites

- The live gateway and QPM checks in the
  [gateway-driver recipe](test-live-gateway-driver.md) pass.
- `PlugStackConfig` names a plugstack file containing the required
  `spank_quantum.so` entry.
- `EpilogSlurmctld` names the installed `qfw-slurm-epilog` executable.
- `plugin.conf` is root-owned, not group- or world-writable, and readable by
  remote `slurmstepd` processes and the controller's `SlurmUser`.

## Reserve and inspect the task environment

Start an interactive allocation:

```bash
salloc --nodes=1 --ntasks=1 --time=00:05:00
```

Launch one managed step:

```bash
srun --qpu=nwqsim \
  --workload-kind=quantum \
  --circ-count=1 \
  --max-qubits=5 \
  --max-depth=20 \
  --max-shots=64 \
  /bin/sh -c 'test -n "${QFW_RESERVATIONS}" && \
    printf "%s\n" "${QFW_RESERVATIONS}"'
```

The task must print canonical JSON containing the configured QPM service ID
and a decimal-string reservation ID. Run a second step with the same QPU to
confirm retrieval without another QPM reservation:

```bash
srun --qpu=nwqsim \
  /bin/sh -c 'printf "%s\n" "${QFW_RESERVATIONS}"'
```

Both steps must print the same tuple set.

## Terminate and verify release

```bash
printf 'job ID: %s\n' "${SLURM_JOB_ID}"
exit

job_id=12345  # Replace with the copied job ID.
qfw-slurm-gateway \
  --config /etc/qfw-slurm/gateway.yaml \
  status "${job_id}"
```

The allocation and all reservation rows must be `released`. An unresolved
row is retained for operator retry:

```bash
qfw-slurm-gateway-launch \
  --config /etc/qfw-slurm/gateway.yaml \
  retry-release "${job_id}"
```
