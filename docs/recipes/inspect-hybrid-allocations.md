# Inspect Hybrid Allocations with qfw-squeue

## Purpose

Use `qfw-squeue` to correlate active Slurm jobs with sanitized QPM allocation
state.

## Prerequisites

The QFw site directory and qfw-slurm integration must be running. Activate the
same QFw installation used by the cluster service plane.

## Procedure

```bash
source /opt/openqse/qfw/bin/qfw-activate \
    --venv /opt/openqse/qfw-venv
qfw-squeue
```

Filter the live view by canonical job ID or owner.

```bash
qfw-squeue --job 42
qfw-squeue --user user-a
```

Use the versioned JSON response for automation.

```bash
qfw-squeue --json
```

See `man qfw-squeue` for options and state definitions.

## Verification

An accepted hybrid allocation displays its QPM service under `QPU`. The
`QSTATE` column follows the allocation lifecycle independently from the Slurm
job state.

## Recovery

If `QSTATE` is `UNAVAILABLE`, compare the QPM and service-node state with
`qfw-sinfo`. Partial-output diagnostics identify an unavailable Slurm,
directory, or QPM data source.
