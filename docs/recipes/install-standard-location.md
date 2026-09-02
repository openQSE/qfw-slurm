# Install qfw-slurm in the Standard Site Location

Use an immutable, versioned native prefix and publish it through a stable
`current` link. This recipe uses `/opt/openqse/qfw-slurm/current`. The gateway
Python package is installed into the version-matched QFw environment because
it imports QFw and DEFw client modules.

The SPANK module must be compiled against the Slurm headers used by the target
cluster. Install the native tree consistently on the controller and every
compute node, or place the prefix on shared storage.

Run `man -l man/man1/qfw_slurm_install.sh.1` from the source tree for the
installer's complete option and dependency contract.

## 1. Select the release paths

```bash
export QFW_SLURM_SRC=/path/to/qfw-slurm
export QFW_SLURM_VERSION=v0.1.0
export QFW_SLURM_BUILD="${HOME}/.cache/qfw-slurm/build-${QFW_SLURM_VERSION}"
export QFW_SLURM_RELEASE_PREFIX="/opt/openqse/qfw-slurm/releases/${QFW_SLURM_VERSION}"
export QFW_SLURM_CURRENT=/opt/openqse/qfw-slurm/current
export QFW_VENV=/opt/openqse/qfw/venvs/v0.1.0
```

## 2. Prepare the site-owned prefix and Python environment

Run these commands as the account responsible for the installation. The QFw
environment must already exist.

```bash
sudo install -d -o "${USER}" -g "$(id -gn)" \
  "$(dirname "${QFW_SLURM_RELEASE_PREFIX}")"

source "${QFW_VENV}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install \
  -r "${QFW_SLURM_SRC}/setup/build-requirements.txt" \
  -r "${QFW_SLURM_SRC}/setup/requirements.txt"
```

## 3. Install the versioned release

```bash
cd "${QFW_SLURM_SRC}"
./setup/qfw_slurm_install.sh \
  --build-dir "${QFW_SLURM_BUILD}" \
  --prefix "${QFW_SLURM_RELEASE_PREFIX}" \
  --python "${QFW_VENV}/bin/python"
```

Do not install another build over this release directory. Use a new versioned
prefix so files removed by a later release cannot survive from an older one.

## 4. Publish the stable path

```bash
sudo ln -s "${QFW_SLURM_RELEASE_PREFIX}" "${QFW_SLURM_CURRENT}.new"
sudo mv -Tf "${QFW_SLURM_CURRENT}.new" "${QFW_SLURM_CURRENT}"
```

This fails when `current` is a real directory rather than a symbolic link.
Migrate that layout during a maintenance window instead of overwriting it.

## 5. Continue with site configuration

The installation includes protected configuration examples and a systemd unit
but does not deploy them into `/etc` or start services. Locate the installed
artifacts with:

```bash
find -L "${QFW_SLURM_CURRENT}" \
  \( -name spank_quantum.so \
     -o -name qfw-slurm-gateway.service \
     -o -path '*/share/qfw-slurm/config/*' \) -print
```

Copy and secure the site files, configure `PlugStackConfig`,
`JobSubmitPlugins=lua`, and `BurstBufferType=burst_buffer/lua`, then follow the
[live gateway recipe](test-live-gateway-driver.md) and
[SPANK integration recipe](test-spank-integration.md). Command and
configuration details are in `qfw-slurm(7)`, `qfw-slurm-plugin.conf(5)`, and
`qfw-slurm-gateway.yaml(5)`.

<details>
<summary>Installation verification and environment cleanup</summary>

```bash
readlink -f "${QFW_SLURM_CURRENT}"
test -x "${QFW_SLURM_CURRENT}/bin/qfw-slurm-driver"
test -x "${QFW_SLURM_CURRENT}/libexec/qfw-slurm/qfw-slurm-bb"
test -r "${QFW_SLURM_CURRENT}/share/man/man1/qfw-slurm-driver.1"
test -r "${QFW_SLURM_CURRENT}/share/man/man7/qfw-slurm.7"
find -L "${QFW_SLURM_CURRENT}" -name spank_quantum.so -print -quit |
  grep -q .

PYTHONPATH= python -c \
  'import qfw_slurm_gateway; print(qfw_slurm_gateway.__file__)'

MANPATH="${QFW_SLURM_CURRENT}/share/man" man 1 qfw-slurm-driver
ctest --test-dir "${QFW_SLURM_BUILD}" --output-on-failure
deactivate
```

</details>
