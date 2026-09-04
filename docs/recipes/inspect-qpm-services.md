# Inspect QPM Services with qfw-sinfo

## Purpose

Use `qfw-sinfo` to compare the state of each long-running QPM with the Slurm
state of its service hosts.

## Prerequisites

The site directory service and at least one QPM should be running. The QFw
installation and qfw-slurm package must be installed in the selected Python
environment.

## Procedure

Activate the site installation and run the summary command.

```bash
source /opt/openqse/qfw/bin/qfw-activate \
    --venv /opt/openqse/qfw-venv
qfw-sinfo
```

Inspect one QPM or DVM host in detail.

```bash
qfw-sinfo nwqsim-head
```

Use the versioned response for automation.

```bash
qfw-sinfo --json
```

See `man qfw-sinfo` for the full command contract.

## Verification

Ready QPMs appear as `IDLE` or `BUSY`. The separate `SLURM_STATE` column
describes host scheduling state and can remain `IDLE` while the QPM is busy.

## Recovery

A `DOWN` QPM or partial-output diagnostic indicates that the directory, QPM,
or Slurm data source was unavailable. An administrator can inspect the site
service plane with `qfw-site-services status` and restart only the failed
component.
