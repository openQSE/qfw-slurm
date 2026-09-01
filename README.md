# qfw-slurm

`qfw-slurm` connects Slurm allocation lifecycle events to QFw QPM
reservations. It remains separate from QFw so the QFw build has no Slurm
dependency.

The repository provides:

- `spank_quantum.so`, which collects bounded workload requirements and exports
  accepted reservation tuples to managed tasks.
- `qfw-slurm-gateway`, which authenticates requests, verifies Slurm jobs,
  discovers QPM services through DEFw, and maintains the SQLite journal.
- `qfw-slurm-epilog`, which releases allocation reservations from the Slurm
  controller.
- `qfw-slurm-driver`, which exercises the shared lifecycle operations without
  loading SPANK or linking with libslurm.

Native and Python components communicate through QSGP version 1 using bounded,
network-byte-order records protected by MUNGE.

## Documentation

Manual pages are the authoritative usage reference:

```bash
man 7 qfw-slurm
man 1 qfw-slurm-driver
man 8 qfw-slurm-gateway
man 8 qfw-slurm-gateway-launch
man 8 qfw-slurm-epilog
man 5 qfw-slurm-plugin.conf
man 5 qfw-slurm-gateway.yaml
```

The [test recipe index](docs/recipes/README.md) provides complete procedures
for native tests, deterministic gateway testing, live DEFw/QPM testing, and
SPANK integration testing.

## Build and install

The full native build requires a C11 compiler, CMake 3.20 or newer, Slurm
development files, libmunge, and MUNGE development headers. Gateway tests also
require Python 3.10 or newer, PyYAML, and pytest.

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
ctest --test-dir build --output-on-failure
sudo cmake --install build --prefix /usr
```

Install the Python gateway in the QFw virtual environment selected by the
site:

```bash
source /opt/openqse/qfw/bin/qfw-activate \
  --venv /opt/openqse/qfw-venv
python -m pip install .
qfw-deactivate
```

Installed examples for the protected site files are under
`share/qfw-slurm/config`. The packaged systemd unit is
`qfw-slurm-gateway.service`.
