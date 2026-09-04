# QFw Slurm Allocation Workflow Report

## Scope

This report evaluates the allocation workflow described by
`docs/detailed-design-workflow.md`, including the gateway-only
`QFW_GW_GET_RESERVATIONS` handoff. Validation used Slurm 25.05.0, MUNGE,
the site-owned DEFw directory service, and a long-running NWQSim QPM in the
virtual QFw Slurm cluster. No real-IQM request was submitted and no provider
credential was read, copied, printed, or changed.

The final validation image was `qfw-slurm-cluster-doug:dougv01`, image ID
`sha256:b6105dd1dff2896dfa7e63207506e40ce55b4077a6b7c645d6fa37ccd1240dbf`.
It built QFw commit `84e006bd9ec7de48886ed2a3e029e15fc190cd83`
and qfw-slurm commit `e996716eed2009a9110d029103c0c3002ed6a066`
from their upstream `release/v0.1` branches. The tested qfw-slurm runtime
implementation is `8ddf202dd86041a7f7286fc9099f54dd9736dc14`; subsequent
qfw-slurm commits install the license and update documentation without
changing that runtime.

The checklist audit preserved these starting repository states:

| Repository | Starting release commit |
| --- | --- |
| qfw-slurm | `cf708c5a324c624f2ed060cafe182f57c17346a2` |
| QFw-SLURM-Cluster | `4206ad028acf8df4c8749b513ef0fa9336cd5f75` plus the validated integration refinements later amended into that commit |
| QFw | `90d245ca9f42d44d50803a8cd9a3245199a69fc1` |

The published release implementation references after validation are
`8ddf202dd86041a7f7286fc9099f54dd9736dc14` for qfw-slurm,
`bf2533de7a9d30bf4a7ec39fee5cbde71dbd837f` for QFw-SLURM-Cluster,
and `84e006bd9ec7de48886ed2a3e029e15fc190cd83` for QFw. The final
qfw-slurm documentation commit follows the implementation reference without
changing installed runtime code.

## Implemented Workflow

The completed path has these boundaries.

1. `salloc` and `sbatch` accept bounded quantum requirements. `srun` does not.
2. `job_submit.lua` validates metadata and maps each public QPU name to one
   exact QPM service ID without network traffic.
3. Slurm repeatedly invokes non-binding evaluation through
   `burst_buffer.lua`. Each invocation produces one gateway request and one
   QPM evaluation; the gateway does not poll.
4. After classical nodes are selected, pre-run makes the final reservation.
   Delay returns the nodes and retries; acceptance is written to the gateway's
   protected SQLite journal.
5. Remote SPANK sends `QFW_GW_GET_RESERVATIONS` with its observed job identity.
   The gateway authenticates MUNGE, verifies the owner and active job through
   Slurm, canonicalizes heterogeneous component IDs, and reads only the
   journal.
6. Remote SPANK injects the returned compact tuple JSON into
   `QFW_RESERVATIONS`. It never evaluates, reserves, extends, releases, or
   contacts the directory service or QPM.
7. Burst-buffer teardown attempts allocation-wide release and returns control
   to Slurm even when provider cleanup needs later operator recovery.

The gateway journal and burst-buffer retry records are controller-local.
Compute nodes contain the protected plugin configuration needed to contact the
gateway, but they do not contain or mount either state directory. QFw may
separately use shared storage for its directory connection record or a PRTE
DVM URI; those artifacts are not the qfw-slurm reservation handoff.

## Automated Validation

A clean, read-only source mount was built inside the release image with
`-Wall`, `-Wextra`, `-Werror`, and `-Wpedantic`. All ten CTest entries passed.

| Test | Result | Principal coverage |
| --- | --- | --- |
| `job_submit_lua` | Passed | Allocator metadata, resource mapping, malformed input |
| `burst_buffer_lua` | Passed | Stage-in, pre-run, teardown, heterogeneous metadata |
| `qsgp_protocol` | Passed | All QSGP messages, bounds, malformed frames, C/Python interoperation |
| `qfw_native` | Passed | Shared operations and canonical tuple JSON |
| `gateway_client` | Passed | MUNGE framing, peer identity, deadlines, correlation |
| `driver_cli` | Passed | Evaluate, reserve, lookup, release, and diagnostics |
| `qfw_gateway` | Passed | Slurm verification, journal lookup, authorization, replay, rollback |
| `qfw_slurm_bb_helper` | Passed | Controller state, delay, retry exhaustion, release recovery |
| `driver_gateway_system` | Passed | Native client to authenticated Python gateway lifecycle |
| `qfw_slurm_install_tree_smoke` | Passed | Installed commands, plugin, Lua, manuals, configuration, license |

The suite was also run from a read-only source mount. Removing test-time
`chmod()` calls from already-executable fixtures prevents tests from mutating
the checkout.

## Live Cluster Results

### Fresh final image

The recreated cluster advertised four CPUs on every four-CPU compute
container, used `select/cons_tres` with exclusive nodes, loaded
`job_submit/lua`, `burst_buffer/lua`, and `spank_quantum.so`, and validated one
MUNGE credential across every node. Controller state paths were absent on all
eight compute nodes.

The root-owned directory service ran on `slurmctld`. The site-owned NWQSim QPM
and PRTE DVM ran on c5. The QPM process remained PID 294 across the application
tests.

| Scenario | Evidence | Result |
| --- | --- | --- |
| Normal allocation | user-a job 1 received `[["nwqsim","1"]]`; the three-qubit Qiskit example returned 1,024 shots and `status: ok`; journal state became `released` | Passed |
| Heterogeneous allocation | user-b canonical job 2 placed quantum metadata on group 0; groups 0 and 1 both received `[["nwqsim","2"]]`; the three-qubit example returned 1,024 shots; journal state became `released` | Passed |
| Additional non-root user | user-c job 4 received `[["nwqsim","3"]]`, completed the three-qubit example with `status: ok`, and released reservation 3 | Passed |
| Login and filesystem isolation | user-a, user-b, and user-c received private mode-0700 homes and per-user run roots; compute nodes had no controller qfw-slurm state paths | Passed |

### Failure and race scenarios

The following cases were exercised against the same implementation before the
final image-only directory-placement refinement.

| Scenario | Evidence | Result |
| --- | --- | --- |
| Classical nodes unavailable | A four-node blocker occupied c1-c4. The competing quantum job remained pending without an assigned node and gateway status was `not-found`, proving that no final reservation existed | Passed |
| Repeated steps | Two `srun` steps in user-b job 2 both received `[["nwqsim","2"]]`; no second QPM reservation was created | Passed |
| Gateway outage and recovery | The gateway and supervisor were stopped after user-a job 9 had reservation 6. The first step failed closed. After restart, a later step in the same allocation received `[["nwqsim","6"]]` | Passed |
| Owner authorization | A MUNGE-authenticated lookup for active user-a job 10 that claimed user-b UID/GID failed with `request identity differs from Slurm`; the correct owner succeeded | Passed |
| Released and unknown jobs | Lookup after job 10 cancellation failed because Slurm reported `CANCELLED`; job 999999 failed as unknown; reservation 7 was journaled as `released` | Passed |
| Cancellation | Canceling an accepted allocation invoked best-effort teardown and produced terminal released journal state | Passed |
| Capacity-one competition | Deterministic live-Slurm validation admitted one competing job, returned the losing job's node after delayed final reserve, and terminated it after the configured retry bound | Passed |
| Permanent deterministic rejection | The deterministic admission adapter rejected the allocation before application execution | Passed |

## Findings

### Oversized NWQSim requests are not structured permanent decisions

An oversized live NWQSim request reached qhw-admission, which raised an
`AdmissionError` through DEFw instead of returning a structured permanent
rejection. qfw-slurm therefore treated the failure as retryable. It created no
reservation, returned the selected node, and left termination to the bounded
final-reservation attempt policy.

The deterministic gateway test proves qfw-slurm's permanent-rejection mapping,
but the live provider boundary cannot distinguish this admission error from a
temporary QPM failure until QPMd returns a structured rejection. This is an
upstream QPM admission-contract issue, not a reason to guess permanence in the
gateway.

### Heterogeneous identity is canonicalized by the gateway

Slurm exposes a component job ID to each remote callback. The lookup verifier
queries Slurm and maps that observed ID to the canonical heterogeneous job ID.
Both components consequently retrieve the same journal row and tuple set.
Controller-local component records are used only for Slurm callback retry and
release bookkeeping.

### Shared storage is not used for reservation delivery

The earlier accepted-state `.env` files and component aliases were removed.
No remote code opens controller reservation state. The fresh image creates the
gateway journal, logs, and burst-buffer retry directory only in the
`slurmctld` entrypoint, and none of those paths exists on compute nodes.

## Coverage Not Claimed

- A live allocation spanning more than one QPM. Atomic rollback is covered by
  gateway tests, while the cluster exposes only one NWQSim site service.
- A guarded real-IQM hardware submission. This validation intentionally used
  no hardware credential.
- Expiration after a gateway and QPM remain unreachable beyond the allocation
  lifetime.
- The full QFw example matrix. The final gate used the short Qiskit example in
  normal and heterogeneous placements.

These are explicit future operational tests. They do not change the validated
gateway-only reservation lookup or the no-shared-state contract.
