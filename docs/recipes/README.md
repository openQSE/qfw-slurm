# qfw-slurm Test Recipes

These recipes validate progressively larger qfw-slurm boundaries. Start with
the native suite, then exercise the gateway, and use the live recipes only on
a configured Slurm cluster.

## Installation

| Goal | Recipe |
| --- | --- |
| Install a development or user-owned build | [Non-standard installation](install-nonstandard-location.md) |
| Install an immutable site release | [Standard site installation](install-standard-location.md) |

## Testing

| Goal | Recipe |
| --- | --- |
| Build and run the native and Python tests | [Native and gateway test suite](test-native-suite.md) |
| Exercise the driver against an isolated deterministic gateway | [Deterministic driver lifecycle](test-deterministic-driver.md) |
| Exercise the driver against a live DEFw directory and QPM | [Live gateway and QPM lifecycle](test-live-gateway-driver.md) |
| Validate allocation-time admission, application context, and teardown | [Live Slurm integration](test-spank-integration.md) |

Recipes provide complete procedures. Command options and lifecycle contracts
are maintained in the installed manual pages:

```bash
man 1 qfw-slurm-driver
man 1 qfw_slurm_install.sh
man 8 qfw-slurm-gateway
man 8 qfw-slurm-gateway-launch
man 8 qfw-slurm-bb
man 7 qfw-slurm
```

Configuration references are in section 5:

```bash
man 5 qfw-slurm-plugin.conf
man 5 qfw-slurm-gateway.yaml
man 5 qfw-slurm-burst-buffer.conf
```
