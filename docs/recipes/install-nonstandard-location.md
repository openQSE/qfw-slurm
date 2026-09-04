# Install qfw-slurm in a Non-standard Location

Use this recipe for a user-owned development or release-candidate installation.
The native prefix must be visible at the same path on every Slurm node that
loads the plugin. The selected Python environment must already contain the
matching QFw and DEFw installation.

Run `man -l man/man1/qfw_slurm_install.sh.1` from the source tree for the
installer's complete option and dependency contract.

## 1. Select paths

```bash
export QFW_SLURM_SRC=/path/to/qfw-slurm
export QFW_SLURM_BUILD="${HOME}/.cache/qfw-slurm/build-v0.1"
export QFW_SLURM_PREFIX="${HOME}/.local/qfw-slurm-v0.1"
export QFW_VENV=/path/to/qfw-venv
```

## 2. Prepare the QFw Python environment

```bash
source "${QFW_VENV}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install \
  -r "${QFW_SLURM_SRC}/setup/build-requirements.txt" \
  -r "${QFW_SLURM_SRC}/setup/requirements.txt"
```

The installer uses this interpreter only for the Python gateway package. It
does not build or replace QFw.

## 3. Build and install

```bash
cd "${QFW_SLURM_SRC}"
./setup/qfw_slurm_install.sh \
  --build-dir "${QFW_SLURM_BUILD}" \
  --prefix "${QFW_SLURM_PREFIX}" \
  --python "${QFW_VENV}/bin/python"
```

The script delegates the native build and installation to CMake, then installs
the gateway and inspection clients into the explicitly selected Python
environment. Run it with `--without-plugin` on a development host that lacks
Slurm headers. That mode installs the driver and lifecycle helper but omits
SPANK.

The installation does not edit `/etc/slurm`, create service accounts, or start
the gateway. Those remain site-administrator operations.

<details>
<summary>Installation verification</summary>

```bash
test -x "${QFW_SLURM_PREFIX}/bin/qfw-slurm-driver"
test -x "${QFW_SLURM_PREFIX}/libexec/qfw-slurm/qfw-slurm-bb"
test -r "${QFW_SLURM_PREFIX}/share/man/man7/qfw-slurm.7"
find "${QFW_SLURM_PREFIX}" -name spank_quantum.so -print -quit |
  grep -q .

PYTHONPATH= python -c \
  'import qfw_slurm_gateway, qfw_slurm_inspect'
command -v qfw-sinfo
command -v qfw-squeue
qfw-sinfo --help >/dev/null
qfw-squeue --help >/dev/null

MANPATH="${QFW_SLURM_PREFIX}/share/man" man 7 qfw-slurm
MANPATH="${QFW_SLURM_PREFIX}/share/man" man 1 qfw-sinfo
MANPATH="${QFW_SLURM_PREFIX}/share/man" man 1 qfw-squeue
ctest --test-dir "${QFW_SLURM_BUILD}" --output-on-failure
```

Run `man 7 qfw-slurm` for the installed lifecycle reference. Leave the
build-time environment with:

```bash
deactivate
```

</details>
