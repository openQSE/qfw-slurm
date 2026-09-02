# qfw-slurm

`qfw-slurm` connects Slurm allocation lifecycle events to QFw QPM
reservations. It remains separate from QFw so the QFw build has no Slurm
dependency.

The repository provides:

- `spank_quantum.so`, which collects bounded requirements at `salloc` or
  `sbatch` time and exports already accepted reservation tuples to tasks.
- Slurm job-submit and burst-buffer Lua providers, which evaluate QPM
  admission before node assignment, reserve after node assignment, and
  release during allocation teardown.
- `qfw-slurm-gateway`, which authenticates requests, verifies Slurm jobs,
  discovers QPM services through DEFw, and maintains the SQLite journal.
- `qfw-slurm-driver`, which exercises the shared lifecycle operations without
  loading SPANK or linking with libslurm.

Native and Python components communicate through QSGP version 1 using bounded,
network-byte-order records protected by MUNGE.

## Documentation

Manual pages are the authoritative usage reference:

```bash
man 7 qfw-slurm
man 1 qfw-slurm-driver
man 1 qfw_slurm_install.sh
man 8 qfw-slurm-gateway
man 8 qfw-slurm-gateway-launch
man 8 qfw-slurm-bb
man 5 qfw-slurm-plugin.conf
man 5 qfw-slurm-gateway.yaml
man 5 qfw-slurm-burst-buffer.conf
```

The [recipe index](docs/recipes/README.md) provides standard and non-standard
installation procedures plus complete native, gateway, QPM, and SPANK test
workflows.

## Build and install

The full native build requires a C11 compiler, CMake 3.20 or newer, Slurm
development files, libmunge, and MUNGE development headers. Gateway tests also
require Python 3.10 or newer, PyYAML, and pytest.

Install build requirements into the QFw virtual environment selected by the
site, then use the source-tree installer:

```bash
python -m pip install \
  -r setup/build-requirements.txt \
  -r setup/requirements.txt

./setup/qfw_slurm_install.sh \
  --build-dir build \
  --prefix /usr \
  --python "$(command -v python)"
```

The installer delegates native installation to CMake and installs the gateway
into only the selected Python environment. See the recipe index for versioned
site prefixes and verification procedures. Installed examples for protected
site files are under `share/qfw-slurm/config`; the packaged service unit is
`qfw-slurm-gateway.service`.
