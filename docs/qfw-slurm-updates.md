# QFw Slurm Allocation Workflow Update Checklist

This checklist records completion of the gateway reservation lookup, virtual
cluster packaging, end-to-end validation, operator documentation, and upstream
release work. Detailed evidence and explicit coverage limits are in
[workflow-report.md](workflow-report.md).

## 1. Preserve the starting state

- [x] Confirm all three repositories use `release/v0.1`.
- [x] Record the starting and final implementation commits.
- [x] Preserve unrelated operator files and existing protected credentials.
- [x] Build Slurm plugins against the exact installed Slurm 25.05.0 source.

## 2. License and installation

- [x] Install the BSD 3-Clause license through CMake.
- [x] Declare the license in `pyproject.toml`.
- [x] Verify the installed license in the installation smoke test.

## 3. QSGP reservation lookup

- [x] Add bounded `QFW_GW_GET_RESERVATIONS` request and response messages.
- [x] Carry request correlation, cluster, observed job, UID, and GID.
- [x] Return only canonical allocation identity and accepted service/reservation
      tuples.
- [x] Keep reservation IDs as wire `uint64_t` values and JSON decimal strings.
- [x] Reject malformed, conflicting, unknown, inactive, released, or
      unauthorized lookups.
- [x] Cover C/Python framing, bounds, and protocol interoperation.

## 4. Gateway-only journal lookup

- [x] Authenticate every request with MUNGE.
- [x] Query Slurm for authoritative owner and active allocation state.
- [x] Canonicalize normal and heterogeneous observed job IDs.
- [x] Read accepted tuples from the SQLite journal without contacting DEFw,
      QPMd, or qhw-admission.
- [x] Keep lookup free of reservation and idempotency side effects.
- [x] Test owner mismatch, unknown jobs, inactive jobs, invalid journal data,
      and repeated lookup.

## 5. Native and SPANK integration

- [x] Add native client and shared operation APIs for reservation lookup.
- [x] Validate response type, correlation, service IDs, IDs, and duplicates.
- [x] Make remote SPANK perform one lookup and inject `QFW_RESERVATIONS`.
- [x] Fail the application step closed when lookup or injection fails.
- [x] Keep all evaluation, reservation, extension, polling, and release work
      outside remote SPANK.
- [x] Confirm a later step succeeds after gateway recovery.

## 6. Remove the shared reservation handoff

- [x] Remove accepted `*.env` files and remote file reads.
- [x] Remove filesystem aliases used only to propagate accepted tuples.
- [x] Keep burst-buffer retry state protected and controller-local.
- [x] Keep the gateway journal protected and controller-local.
- [x] Retrieve heterogeneous component context through canonical gateway
      lookup.
- [x] Confirm compute nodes do not contain or mount either controller state
      path.

## 7. Documentation

- [x] Maintain `detailed-design-workflow.jsonl` as the structured source.
- [x] Render and inspect `detailed-design-workflow.md`.
- [x] Show gateway-only lookup in the sequence diagram and prose.
- [x] Update qfw-slurm manuals, examples, installation, and test recipes.
- [x] Update QFw recipes for cluster setup, site services, normal and
      heterogeneous allocation, application execution, verification, and
      recovery.
- [x] Distinguish QFw shared directory/DVM artifacts from controller-local
      qfw-slurm state.

## 8. Automated validation

- [x] Build all C targets with `-Wall -Wextra -Werror -Wpedantic`.
- [x] Pass job-submit and burst-buffer Lua tests.
- [x] Pass native protocol, operation, client, and driver tests.
- [x] Pass the complete Python gateway suite.
- [x] Pass the MUNGE native-to-gateway system test.
- [x] Pass the installed-tree smoke test.
- [x] Pass all ten CTest entries from a read-only source mount.

## 9. qfw-slurm branches

- [x] Configure `git@github.com:openQSE/qfw-slurm.git` as upstream.
- [x] Create and publish `main`.
- [x] Publish `release/v0.1`.
- [x] Keep both branches at the same validated release content.
- [x] Fold validation fixes into their originating functional commits.

## 10. Virtual cluster packaging

- [x] Build `job_submit/lua` and `burst_buffer/lua` from the exact Slurm
      source tag.
- [x] Clone, build, test, and install qfw-slurm under
      `/opt/openqse/qfw-slurm`.
- [x] Install the plugin, Lua providers, helper, driver, gateway, manuals, and
      license.
- [x] Install the Python gateway into the official QFw environment.
- [x] Provision protected plugin, resource, lifecycle, and gateway
      configuration.
- [x] Configure `JobSubmitPlugins=lua`, `BurstBufferType=burst_buffer/lua`,
      and the SPANK plugstack.
- [x] Supervise the DEFw gateway on `slurmctld`.
- [x] Resolve upstream release refs before builds so Docker cache cannot hide a
      moved branch.
- [x] Create controller state directories only in the controller entrypoint.

## 11. Cluster identity and site services

- [x] Provision user-a, user-b, and user-c with stable UID/GID values.
- [x] Provision mode-0700 shared homes and per-user application run roots.
- [x] Install the login profile and verify its QFw convenience variables.
- [x] Verify MUNGE across controller and compute nodes.
- [x] Start a root-owned directory service on `slurmctld`.
- [x] Start a root-owned NWQSim QPM and PRTE DVM on c5.
- [x] Verify gateway, directory, QPM, simulator modules, and ports.

## 12. End-to-end allocation validation

- [x] Run normal non-root allocations as user-a, user-b, and user-c.
- [x] Run a real QFw Qiskit example through `qfw-setup` and `qfw-srun`.
- [x] Verify repeated steps reuse one gateway-journaled reservation.
- [x] Run a heterogeneous allocation and verify both groups receive the same
      canonical tuple.
- [x] Hold all classical nodes and verify no final QPM reservation is created.
- [x] Exercise capacity-one competition and bounded final-reserve retry.
- [x] Exercise deterministic permanent admission rejection.
- [x] Reject cross-user, unknown, and released-allocation lookup.
- [x] Stop the gateway, verify fail-closed step launch, restart it, and verify
      later success in the same allocation.
- [x] Cancel an accepted allocation and verify terminal release state.
- [x] Verify homes and application run roots remain isolated.

## 13. Findings retained for follow-up

- [x] Record that the live NWQSim admission boundary raises an exception for an
      oversized request instead of returning structured permanent rejection.
- [x] Do not guess permanence in qfw-slurm; retain bounded retry and record the
      QPM admission-contract gap.
- [x] Record live multi-QPM, long-outage expiration, real-IQM, and full example
      matrix coverage as unclaimed rather than implying they passed.

## 14. Publish affected repositories

- [x] Commit and push QFw recipe changes on `release/v0.1`.
- [x] Amend, commit, and push QFw-SLURM-Cluster integration on
      `release/v0.1`.
- [x] Push qfw-slurm `main` and `release/v0.1`.
- [x] Verify remote branch hashes after publication.

## 15. Final upstream rebuild

- [x] Build the release image from upstream QFw and qfw-slurm release refs.
- [x] Recreate the cluster from the image.
- [x] Repeat one normal and one heterogeneous NWQSim application.
- [x] Verify official installations and manuals.
- [x] Verify no credential appears in commits, logs, reports, layers, or
      application environments.
- [x] Verify reservation delivery has no shared-filesystem dependency.
