# QFw Slurm Allocation Workflow Report

## Scope

This report evaluates the implementation of
`docs/detailed-design-workflow.md` on the virtual Slurm cluster. All source
changes are confined to the `qfw-slurm` repository. The validation used the
site-owned DEFw directory service and long-running NWQSim QPM already available
in the cluster. It did not change QFw, DEFw, the Slurm cluster repository, or
hardware credential files.

The implementation was validated on 2026-09-02 against Slurm 25.05.0. The
running container image was `qfw-slurm-cluster-doug:dougv01`, container ID
`0047f5d24866`. Compute nodes exposed four CPUs each and the normal partition
used `select/cons_tres` with exclusive node sharing.

## Implemented Workflow

The completed qfw-slurm path has the following behavior.

1. Allocator SPANK options are accepted by `salloc` and `sbatch` only.
2. `job_submit.lua` validates the option metadata, maps public QPU names to
   exact QPM service IDs, and produces a bounded internal directive without
   network I/O.
3. `burst_buffer.lua` performs non-binding evaluation before node assignment.
   Slurm owns the polling loop.
4. Pre-run performs one final reservation attempt after all classical nodes
   are assigned. A delayed result returns the nodes and retries the allocation.
5. A protected SlurmUser-owned state record stores the accepted reservation
   tuples. Remote SPANK reads that record and injects `QFW_RESERVATIONS`; it
   performs no gateway or reservation operation.
6. Burst-buffer teardown performs best-effort allocation-wide release. It can
   recover through the gateway journal when accepted local state is missing.
7. Final reservation attempts are bounded. Exhaustion becomes a permanent
   rejection instead of cycling indefinitely.
8. Heterogeneous component aliases resolve to the canonical allocation ID for
   release, so teardown cannot target the wrong Slurm job.

QSGP now has a distinct `QFW_GW_EVALUATE` operation. The gateway calls the QPM
for every evaluation request and never polls independently. Delayed evaluation
is nonterminal. The gateway normalizes the QPM admission API's zero
reservation sentinel as no reservation and rejects any nonzero reservation ID
from evaluation.

The old controller epilog executable and its installation, manuals, and
recipes were removed. Burst-buffer teardown is the only supported allocation
release path.

## Automated Validation

A clean CMake build completed with `-Wall -Wextra -Werror -Wpedantic`. The
installed-tree smoke test also passed after removal of the epilog and addition
of the lifecycle configuration and manuals.

| Test boundary | Result | Coverage |
| --- | --- | --- |
| QSGP native protocol | Passed | Evaluation, reserve, release, bounds, malformed messages, and interoperation |
| Native operation layer | Passed | Request IDs, response classification, canonical reservation JSON, and errors |
| Native gateway client | Passed | MUNGE framing, correlation, deadlines, and peer validation |
| Gateway Python suite | Passed | Authentication, Slurm verification, journal recovery, evaluation, reserve, rollback, and release |
| Burst-buffer helper suite | Passed | Classical-wait state, repeated evaluation, accepted reserve, delayed final reserve, bounded exhaustion, missing-state release, and heterogeneous release |
| Job-submit and burst-buffer Lua tests | Passed in `slurmctld` | Option translation, helper status mapping, heterogeneous metadata, and long job scripts |
| Installed-tree smoke test | Passed | Commands, plugin, Lua files, examples, systemd unit, configuration, and manuals |

The local deterministic driver/gateway CTest was skipped because the host did
not have a running MUNGE service. Its native-to-gateway path and MUNGE identity
checks were exercised inside the live Slurm cluster instead.

## Live Cluster Results

The following tests used real Slurm scheduling and callback placement. A
deterministic gateway was used only where a repeatable admission policy was
needed. The normal application tests used the production gateway, DEFw
directory service, and long-running NWQSim QPM.

| Scenario | Evidence | Result |
| --- | --- | --- |
| Normal allocation, production gateway and NWQSim | Job 81 ran `test_qiskit_simple.py` through `qfw-srun`; NWQSim returned 1,024-shot GHZ counts; reservation 14 became `released` | Passed |
| Classical nodes unavailable | Job 78 occupied c1-c4. Job 79 remained `PENDING (Resources)` with no node, `evaluation-accepted`, zero reservation attempts, and no reservation tuple. After job 78 was canceled, job 79 ran on c1, received reservation 12, and released it | Passed |
| Two jobs competing for one QPM slot | Deterministic capacity-one jobs 67 and 68 both evaluated positively. Job 67 ran with reservation 41. Job 68 returned its node after each delayed final reserve | Passed |
| Final-reservation retry exhaustion | Job 68 made two configured final attempts, remained pending without a node after each delay, then became `CANCELLED by 990` with `reservation-exhausted` | Passed |
| Permanent preliminary rejection | Deterministic job 59 became terminal without an assigned node or QPM reservation | Passed |
| Allocator-only option enforcement | `srun --qpu=nwqsim ...` failed as an unrecognized option | Passed |
| Heterogeneous application in group 0 | Job 60 exported one canonical reservation set to the application group | Passed |
| Heterogeneous application in group 1 | Jobs 75/76 allocated c1/c2; group 1 received `[["nwqsim-site","10"]]`; both canonical and component state records became `released` | Passed |
| Repeated application context | Two steps in one accepted allocation received the same canonical tuple without another gateway reservation | Passed |
| Cancellation after acceptance | Canceling job 67 invoked teardown and changed its accepted state to `released` | Passed |

The classical-starvation test directly confirms the main scheduling goal. A
positive QPM evaluation does not hold quantum capacity while Slurm waits for
classical resources. The final reservation is created only after a classical
node is selected.

The contention test confirms the expected optimistic race. More than one job
may evaluate positively. Only one can acquire the emulated capacity during
final reserve. The losing job does not keep its classical node while waiting.

## Evaluation and Findings

### Slurm 25.05 callback ordering

Source inspection and live testing showed that Slurm calls
`slurm_bb_paths` before asynchronous `slurm_bb_pre_run`. Therefore the paths
hook cannot export a reservation created during pre-run. The implementation
uses a narrowly scoped remote SPANK callback to read protected accepted state
and inject the application environment. It does not restore the retired
step-time reservation workflow.

### Heterogeneous teardown identity

Slurm invokes teardown using the heterogeneous component job ID. The gateway
journal is keyed by the canonical heterogeneous job ID. The initial helper
used the component ID for release, which the gateway correctly rejected. The
fixed helper reads the canonical ID from the protected alias before issuing
release and writes the result to both records. The repeated group-1 test
verified this behavior.

### QPM evaluation sentinel

The current QPM admission binding returns `reservation_id: 0` for a
non-binding evaluation. Zero is the C API's absent-ID sentinel, not a valid
reservation. Treating it as an allocated ID caused an unnecessary operational
retry. The gateway now accepts zero as absent while continuing to reject every
nonzero evaluation reservation ID.

### Cluster image prerequisites

The running image had Lua and MUNGE runtime support after provisioning, but it
did not originally contain Slurm's `job_submit/lua` and `burst_buffer/lua`
shared objects. For validation, those plugins were built from the exact
`slurm-25-05-0-1` source tag and installed transiently. Building from a newer
Slurm source revision produced an ABI/version mismatch and was rejected.

A reproducible cluster image must build or package both Lua plugins against
the exact installed Slurm version. This is a cluster packaging task and was
not committed here because this change set is intentionally confined to
qfw-slurm.

### QFw example wrapper behavior

The low-level QFw example application consumed the plugin-created reservation
and passed. The higher-level `qfw_qiskit_simple.sh` wrapper also passed, but its
development `qfw_slurm_driver.sh` path created and released a second
application reservation. That wrapper therefore validates QFw compatibility,
not exclusive use of the allocation reservation.

Changing the QFw example wrappers to reuse an existing
`QFW_RESERVATIONS` value belongs in QFw and was outside the repository boundary
for this implementation. Until that change is made, the authoritative plugin
workflow test is `qfw-setup`, followed by `qfw-srun` of the example Python
application, followed by `qfw-teardown`.

## Coverage Not Claimed

The following design-matrix cases were not run against live Slurm in this
iteration.

- A multi-QPM allocation with partial final rollback. The gateway unit suite
  covers atomic rollback, but only one live QPM service was provisioned.
- Gateway loss during every individual Slurm phase. Transport and release
  failures are covered below the live scheduler boundary.
- Reservation expiration after a permanently unreachable gateway or QPM.
- A real-IQM hardware submission. No hardware authorization was inferred from
  the request to test on the Slurm cluster, and no credential file was read.
- The complete QFw simulator example matrix. One direct Qiskit/NWQSim
  application and the existing wrapper were run.

These omissions do not invalidate the two requested corner cases or the
implemented allocation lifecycle. They remain explicit operational validation
work before declaring the complete production matrix finished.
