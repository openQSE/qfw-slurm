# Test the Driver Against the Deterministic Gateway

This recipe exercises the standalone driver, QSGP framing, MUNGE
authentication, gateway server, reservation service, and SQLite journal. The
test-only verifier and QPM adapter make the results deterministic. No Slurm
job, directory service, or QPM is required.

Run `man 1 qfw-slurm-driver` for driver commands and exit status definitions.

## Prerequisites

Complete the [native test recipe](test-native-suite.md). Run this test as root
on a host with a functioning MUNGE daemon. The harness skips with status 77
when either requirement is absent.

## Run the lifecycle suite

From the repository root:

```bash
sudo env \
  PYTHONPATH="${PWD}/gateway:${PWD}" \
  QFW_TEST_PYTHON="$(command -v python3)" \
  tests/system/test_driver_gateway.sh \
  "${PWD}/build-test/qfw-slurm-driver"
```

An empty output stream and status zero indicate success. The harness covers
accepted reservation replay and release, delayed and rejected admission, QPM
failure, malformed QPM output, timeout, and unresolved release state.

## Run through an initialized DEFw process

On a QFw test system, enter a root shell through the site's normal
administrative procedure. Activate QFw and prepare the same DEFw
directory-parent environment used by the site gateway, then select `defwp`
explicitly:

```bash
test "$(id -u)" -eq 0
export PYTHONPATH="${PWD}/gateway:${PWD}"
export QFW_TEST_PYTHON="$(command -v python3)"
export QFW_TEST_DEFW_RUNNER="${DEFW_PREFIX}/bin/defwp"
tests/system/test_driver_gateway.sh \
  "${PWD}/build-test/qfw-slurm-driver"
```

This variant hosts each deterministic gateway in DEFw. Use the live gateway
recipe when the intent is to validate actual directory and QPM calls.

## Recovery

The harness creates a private directory below `/tmp` and removes it on normal
exit or a handled signal. If the host is interrupted forcibly, confirm that no
`tests.system.deterministic_gateway` process remains before rerunning.
