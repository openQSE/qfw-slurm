# Run the Native and Gateway Test Suite

This recipe builds qfw-slurm from a clean directory and runs its native,
protocol-interoperability, driver, and Python gateway tests. It does not
contact Slurm or a QPM.

## Prerequisites

Install a C11 compiler, CMake 3.20 or newer, the MUNGE development package,
Python 3.10 or newer, PyYAML, and pytest. A full build also needs the Slurm
development headers and library.

Run `man 7 qfw-slurm` for the component and test boundaries.

## 1. Build and test the full tree

Run from the qfw-slurm repository root:

```bash
cmake -S . -B build-test -DCMAKE_BUILD_TYPE=Release
cmake --build build-test --parallel
ctest --test-dir build-test --output-on-failure
```

The native compiler targets use `-Wall -Wextra -Werror -Wpedantic`. CTest runs
the protocol, native-operation, gateway-client, driver-CLI, and Python gateway
suites. The deterministic system test skips with status 77 when the caller is
not root or MUNGE is unavailable.

## 2. Verify the Slurm-independent build

```bash
cmake -S . -B build-native-test \
  -DCMAKE_BUILD_TYPE=Release \
  -DQFW_SLURM_BUILD_PLUGIN=OFF
cmake --build build-native-test --parallel
ctest --test-dir build-native-test --output-on-failure
```

This build must produce `qfw-slurm-driver` without requiring Slurm headers or
libslurm.

## 3. Run the Python suite directly

```bash
PYTHONPATH=gateway python3 -m pytest -q tests/gateway
```

## Verification

Both CTest invocations must report zero failed tests. The Python invocation
must report only passes and intentional skips.

When a test fails, rerun its exact command with verbose output:

```bash
ctest --test-dir build-test -R TEST_NAME -V
```
