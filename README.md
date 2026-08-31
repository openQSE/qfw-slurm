# qfw-slurm

`qfw-slurm` connects Slurm allocation lifecycle events to QFw QPM
reservations. It is intentionally separate from QFw and does not add a Slurm
dependency to the QFw build.

The repository provides three runtime components:

- `spank_quantum.so` parses bounded workload options and acquires an atomic
  reservation set before a managed task starts.
- `qfw-slurm-epilog` asks the gateway to release every reservation after the
  complete Slurm allocation terminates.
- `qfw-slurm-gateway` authenticates native requests with MUNGE, verifies job
  identity with `slurmctld`, discovers exact QPM service IDs through DEFw, and
  journals QPM reserve and release operations in SQLite.

The native and Python components communicate through QSGP version 1. QSGP
uses an explicit network-byte-order header and bounded TLV records inside a
MUNGE credential. It never sends C memory layouts, Python objects, provider
credentials, or user circuits.

## Build and test

The native build requires the Slurm development headers, libslurm, libmunge,
and the MUNGE development headers. Gateway tests additionally require Python
3.10 or newer, PyYAML, and pytest.

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
ctest --test-dir build --output-on-failure
```

The test suite checks strict native decoding, Python decoding, C/Python wire
interoperability, option validation, durable replay, request conflicts,
allocation-level rollback, exhaustive release, and stale QPM incarnations.

## Install

Install the native artifacts for the Slurm ABI used by the target cluster:

```bash
sudo cmake --install build --prefix /usr
```

Install the gateway package into the Python environment selected for the site
QFw installation:

```bash
source /opt/openqse/qfw/bin/qfw-activate \
    --venv /opt/openqse/qfw-venv
python -m pip install .
qfw-deactivate
```

Create a dedicated `qfw-slurm` account with the same numeric UID and GID on
every node that verifies gateway MUNGE responses. Give that account access to
the MUNGE socket. The provided systemd unit creates and owns
`/var/lib/qfw-slurm-gateway` through `StateDirectory`.

Copy the examples from `/usr/share/qfw-slurm/config` into these protected
site paths:

```text
/etc/qfw-slurm/plugin.conf
/etc/qfw-slurm/gateway.yaml
/etc/qfw-slurm/gateway.env
/etc/slurm/plugstack.conf
```

The first three files must be owned by root and must not be group- or
world-writable. The plugin refuses an unsafe `plugin.conf`, and the gateway
refuses an unsafe `gateway.yaml`. The files contain service mappings and
endpoints, but no QPU credentials. Set `QFW_SHARED_ROOT` in `gateway.env` to
the shared root used by `site.yaml` for its directory-service connection
record.

Configure Slurm to load the required plugin and controller epilog:

```text
PlugStackConfig=/etc/slurm/plugstack.conf
EpilogSlurmctld=/usr/sbin/qfw-slurm-epilog
```

Install and start the provided systemd unit after the site directory service
is available:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now qfw-slurm-gateway.service
sudo systemctl restart slurmctld
```

The launcher reads the directory-service connection record selected by
`site.yaml`, prepares the DEFw parent environment, and then replaces itself
with `defwp`. Invoking the gateway Python module directly does not initialize
the DEFw client runtime.

## Submit a managed workload

Every resource name maps to one exact QPM `service_id` in root-owned
`plugin.conf`. Required workload bounds are separate Slurm options:

```bash
salloc --nodes=1 --ntasks=1 --time=00:15:00 \
    --qpu=nwqsim \
    --workload-kind=quantum \
    --circ-count=2 \
    --max-qubits=5 \
    --max-depth=100 \
    --max-shots=1024

source /opt/openqse/qfw/bin/qfw-activate \
    --venv /opt/openqse/qfw-venv
cd "${QFW_SHARE_DIR}/examples"
./qfw_qiskit_simple.sh --service-mode site --backend nwqsim 5
qfw-deactivate
exit
```

On acceptance, the plugin exports a canonical value such as:

```text
QFW_RESERVATIONS=[["nwqsim-site","41"]]
```

Reservation IDs are decimal strings so the complete `uint64_t` range is
preserved. The gateway, rather than the application, owns the durable journal.
The first managed step must provide all workload bounds. A later step in the
same allocation may provide only the same `--qpu` selection; the gateway then
retrieves the existing reservation set without reserving the QPM again.

## Inspect and recover

The protected status commands may be run as the gateway service account. They
do not expose QPU credentials or MUNGE material:

```bash
sudo -u qfw-slurm qfw-slurm-gateway \
    --config /etc/qfw-slurm/gateway.yaml list

sudo -u qfw-slurm qfw-slurm-gateway \
    --config /etc/qfw-slurm/gateway.yaml status 12345
```

The readiness command must run through DEFw. Repeat `--service` for every QPM
that an operator wants to verify:

```bash
sudo -u qfw-slurm /bin/bash -c '
  source /opt/openqse/qfw/bin/qfw-activate \
      --venv /opt/openqse/qfw-venv
  exec qfw-slurm-gateway-launch \
      --config /etc/qfw-slurm/gateway.yaml \
      check --service nwqsim-site --service iqm-ornl-20q
'
```

Retrying a QPM release requires an initialized DEFw process:

```bash
sudo -u qfw-slurm /bin/bash -c '
  source /opt/openqse/qfw/bin/qfw-activate \
      --venv /opt/openqse/qfw-venv
  exec qfw-slurm-gateway-launch \
      --config /etc/qfw-slurm/gateway.yaml \
      retry-release 12345
'
```

The retry attempts every nonterminal reservation even when an earlier QPM is
unavailable. A stale runtime or generation is reported and retained for
operator review; the gateway never sends an old reservation ID to a new QPM
incarnation.
