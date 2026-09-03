from __future__ import annotations

import dataclasses
import asyncio

from qfw_slurm_gateway.defw_client import QPMBinding
from qfw_slurm_gateway.journal import Journal
from qfw_slurm_gateway.protocol import (
    AdmissionDecision,
    EvaluateRequest,
    EvaluateResponse,
    GatewayError,
    GetReservationsRequest,
    GetReservationsResponse,
    ReleaseRequest,
    ReleaseResponse,
    ReservationState,
    ReserveRequest,
    ReserveResponse,
    ServiceResult,
    Workload,
    WorkloadKind,
)
from qfw_slurm_gateway.service import GatewayService
from qfw_slurm_gateway.slurm_verifier import VerifiedJob


class FakeVerifier:
    def verify_reserve(self, request, sender_uid):
        if sender_uid != request.job_uid:
            raise AssertionError("unexpected sender")
        return VerifiedJob(
            request.cluster_name,
            request.canonical_job_id,
            request.job_uid,
            request.job_gid,
            "user-a",
            "account",
            "normal",
            1,
            "RUNNING",
        )

    def verify_release(self, request, sender_uid, expected_uid):
        assert sender_uid == expected_uid
        return VerifiedJob(
            request.cluster_name,
            request.canonical_job_id,
            sender_uid,
            sender_uid,
            "user-a",
            "account",
            "normal",
            1,
            "COMPLETING",
        )

    def verify_lookup(self, request, sender_uid):
        if sender_uid != request.job_uid:
            raise AssertionError("unexpected sender")
        canonical = 100 if request.observed_job_id in {100, 101} else request.observed_job_id
        return VerifiedJob(
            request.cluster_name,
            canonical,
            request.job_uid,
            request.job_gid,
            "user-a",
            "account",
            "normal",
            1,
            "RUNNING",
        )


class FakeAdapter:
    def __init__(self, decisions=None):
        self.decisions = decisions or {}
        self.reserves = []
        self.evaluates = []
        self.releases = []
        self.generations = {}

    def resolve(self, service_id):
        runtime, generation = self.generations.get(service_id, ("runtime", 1))
        return QPMBinding(service_id, runtime, generation, object())

    def reserve(self, binding, request, job):
        self.reserves.append(binding.service_id)
        decision = self.decisions.get(
            binding.service_id, AdmissionDecision.ACCEPTED
        )
        if isinstance(decision, list):
            decision = decision.pop(0)
        return ServiceResult(
            binding.service_id,
            decision,
            0 if decision == AdmissionDecision.ACCEPTED else 9,
            reservation_id=len(self.reserves) + 39
            if decision == AdmissionDecision.ACCEPTED
            else None,
            qpm_runtime_id=binding.runtime_id,
            qpm_generation=binding.generation,
        )

    def evaluate(self, binding, request, job):
        self.evaluates.append(binding.service_id)
        configured = self.decisions.get(
            binding.service_id, AdmissionDecision.ACCEPTED
        )
        if isinstance(configured, list):
            decision = configured.pop(0)
        else:
            decision = configured
        return ServiceResult(
            binding.service_id,
            decision,
            0 if decision == AdmissionDecision.ACCEPTED else 9,
            retry_after_ns=1_000_000
            if decision == AdmissionDecision.DELAYED
            else None,
            qpm_runtime_id=binding.runtime_id,
            qpm_generation=binding.generation,
        )
    def release(self, binding, reservation_id, reason):
        self.releases.append((binding.service_id, reservation_id, reason))
        return {"status": "accepted"}


class FailingAdapter(FakeAdapter):
    def resolve(self, service_id):
        if service_id == "svc-b":
            raise RuntimeError("directory unavailable")
        return super().resolve(service_id)


class ReleaseFailingAdapter(FakeAdapter):
    def release(self, binding, reservation_id, reason):
        raise RuntimeError("configured release failure")


class CapacityOneAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.held_by = None

    def evaluate(self, binding, request, job):
        self.evaluates.append((job.canonical_job_id, binding.service_id))
        return ServiceResult(
            binding.service_id,
            AdmissionDecision.ACCEPTED,
            0,
            qpm_runtime_id=binding.runtime_id,
            qpm_generation=binding.generation,
        )

    def reserve(self, binding, request, job):
        self.reserves.append((job.canonical_job_id, binding.service_id))
        if (self.held_by is not None and
                self.held_by != job.canonical_job_id):
            return ServiceResult(
                binding.service_id,
                AdmissionDecision.DELAYED,
                9,
                qpm_runtime_id=binding.runtime_id,
                qpm_generation=binding.generation,
            )
        self.held_by = job.canonical_job_id
        return ServiceResult(
            binding.service_id,
            AdmissionDecision.ACCEPTED,
            0,
            reservation_id=40 + job.canonical_job_id,
            qpm_runtime_id=binding.runtime_id,
            qpm_generation=binding.generation,
        )

    def release(self, binding, reservation_id, reason):
        self.releases.append((binding.service_id, reservation_id, reason))
        self.held_by = None
        return {"status": "released"}


def request(request_id=1):
    return ReserveRequest(
        request_id,
        "cluster",
        100,
        1001,
        1001,
        Workload(WorkloadKind.QUANTUM, 100, 2, 5, 10, 20),
        ("svc-a", "svc-b"),
    )


def evaluate_request(request_id=1):
    return EvaluateRequest(**request(request_id).__dict__)


def lookup_request(request_id=9, job_id=100):
    return GetReservationsRequest(request_id, "cluster", job_id, 1001, 1001)


def single_service_request(job_id, request_id, evaluate=False):
    value = dataclasses.replace(
        request(request_id),
        canonical_job_id=job_id,
        service_ids=("svc-a",),
    )
    return EvaluateRequest(**value.__dict__) if evaluate else value


def test_competing_jobs_reenter_evaluation_after_final_delay(tmp_path) -> None:
    asyncio.run(_competing_jobs_reenter_evaluation_after_final_delay(tmp_path))


async def _competing_jobs_reenter_evaluation_after_final_delay(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    adapter = CapacityOneAdapter()
    service = GatewayService(journal, FakeVerifier(), adapter)

    first_evaluation = await service.handle(
        single_service_request(100, 1001, evaluate=True), 1001
    )
    second_evaluation = await service.handle(
        single_service_request(101, 1002, evaluate=True), 1001
    )
    first_reserve = await service.handle(
        single_service_request(100, 1003), 1001
    )
    second_delayed = await service.handle(
        single_service_request(101, 1004), 1001
    )

    assert first_evaluation.decision == AdmissionDecision.ACCEPTED
    assert second_evaluation.decision == AdmissionDecision.ACCEPTED
    assert first_reserve.decision == AdmissionDecision.ACCEPTED
    assert second_delayed.decision == AdmissionDecision.DELAYED
    assert journal.allocation_status("cluster", 101)["state"] == "delayed"

    released = await service.handle(
        ReleaseRequest(1005, "cluster", 100, 0), 1001
    )
    reevaluated = await service.handle(
        single_service_request(101, 1006, evaluate=True), 1001
    )
    second_reserve = await service.handle(
        single_service_request(101, 1007), 1001
    )

    assert isinstance(released, ReleaseResponse)
    assert reevaluated.decision == AdmissionDecision.ACCEPTED
    assert second_reserve.decision == AdmissionDecision.ACCEPTED
    assert adapter.held_by == 101
    journal.close()


def test_evaluate_is_nonbinding_and_terminal_results_replay(tmp_path) -> None:
    asyncio.run(_evaluate_is_nonbinding_and_terminal_results_replay(tmp_path))


async def _evaluate_is_nonbinding_and_terminal_results_replay(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    adapter = FakeAdapter()
    service = GatewayService(journal, FakeVerifier(), adapter)

    first = await service.handle(evaluate_request(), 1001)
    replay = await service.handle(evaluate_request(), 1001)

    assert isinstance(first, EvaluateResponse)
    assert first.decision == AdmissionDecision.ACCEPTED
    assert replay == first
    assert adapter.evaluates == ["svc-a", "svc-b"]
    assert adapter.reserves == []
    assert all(item.reservation_id is None for item in first.results)
    assert journal.allocation_status("cluster", 100)["state"] == "not-found"
    journal.close()


def test_delayed_evaluation_calls_qpm_on_each_poll(tmp_path) -> None:
    asyncio.run(_delayed_evaluation_calls_qpm_on_each_poll(tmp_path))


async def _delayed_evaluation_calls_qpm_on_each_poll(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    adapter = FakeAdapter(
        {
            "svc-a": [
                AdmissionDecision.DELAYED,
                AdmissionDecision.ACCEPTED,
            ]
        }
    )
    service = GatewayService(journal, FakeVerifier(), adapter)

    delayed = await service.handle(evaluate_request(), 1001)
    accepted = await service.handle(evaluate_request(), 1001)

    assert isinstance(delayed, EvaluateResponse)
    assert delayed.decision == AdmissionDecision.DELAYED
    assert isinstance(accepted, EvaluateResponse)
    assert accepted.decision == AdmissionDecision.ACCEPTED
    assert adapter.evaluates.count("svc-a") == 2
    assert adapter.evaluates.count("svc-b") == 2
    journal.close()


def test_atomic_reserve_replay_and_release(tmp_path) -> None:
    asyncio.run(_atomic_reserve_replay_and_release(tmp_path))


async def _atomic_reserve_replay_and_release(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    adapter = FakeAdapter()
    service = GatewayService(journal, FakeVerifier(), adapter)
    first = await service.handle(request(), 1001)
    replay = await service.handle(request(), 1001)
    assert isinstance(first, ReserveResponse)
    assert first.decision == AdmissionDecision.ACCEPTED
    assert replay == first

    retrieval = dataclasses.replace(request(3), workload=None)
    retrieved = await service.handle(retrieval, 1001)
    assert retrieved == dataclasses.replace(first, request_id=3)

    new_operation = request(4)
    reused = await service.handle(new_operation, 1001)
    assert reused == dataclasses.replace(first, request_id=4)

    changed_workload = dataclasses.replace(
        request(5),
        workload=dataclasses.replace(request().workload, max_qubits=6),
    )
    conflict = await service.handle(changed_workload, 1001)
    assert conflict.error_code == GatewayError.REQUEST_CONFLICT

    released = await service.handle(ReleaseRequest(2, "cluster", 100, 3), 1001)
    assert isinstance(released, ReleaseResponse)
    assert {item.state for item in released.results} == {
        ReservationState.RELEASED
    }
    assert len(adapter.releases) == 2
    journal.close()


def test_lookup_reads_accepted_journal_without_qpm_calls(tmp_path) -> None:
    asyncio.run(_lookup_reads_accepted_journal_without_qpm_calls(tmp_path))


async def _lookup_reads_accepted_journal_without_qpm_calls(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    adapter = FakeAdapter()
    service = GatewayService(journal, FakeVerifier(), adapter)
    assert isinstance(await service.handle(request(), 1001), ReserveResponse)
    calls = (list(adapter.reserves), list(adapter.evaluates), list(adapter.releases))

    first = await service.handle(lookup_request(), 1001)
    repeated = await service.handle(lookup_request(10, 101), 1001)

    assert isinstance(first, GetReservationsResponse)
    assert first.canonical_job_id == 100
    assert [(item.service_id, item.reservation_id) for item in first.reservations] == [
        ("svc-a", 40),
        ("svc-b", 41),
    ]
    assert repeated.reservations == first.reservations
    assert calls == (adapter.reserves, adapter.evaluates, adapter.releases)
    journal.close()


def test_lookup_rejects_missing_and_released_allocations(tmp_path) -> None:
    asyncio.run(_lookup_rejects_missing_and_released_allocations(tmp_path))


async def _lookup_rejects_missing_and_released_allocations(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    service = GatewayService(journal, FakeVerifier(), FakeAdapter())
    missing = await service.handle(lookup_request(), 1001)
    assert missing.error_code == GatewayError.ALLOCATION_NOT_FOUND

    assert isinstance(await service.handle(request(), 1001), ReserveResponse)
    assert isinstance(
        await service.handle(ReleaseRequest(2, "cluster", 100, 3), 1001),
        ReleaseResponse,
    )
    released = await service.handle(lookup_request(), 1001)
    assert released.error_code == GatewayError.ALLOCATION_RELEASED
    journal.close()


def test_partial_reserve_is_rolled_back(tmp_path) -> None:
    asyncio.run(_partial_reserve_is_rolled_back(tmp_path))


def test_delayed_final_reserve_retries_with_new_operation(tmp_path) -> None:
    asyncio.run(_delayed_final_reserve_retries_with_new_operation(tmp_path))


async def _delayed_final_reserve_retries_with_new_operation(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    adapter = FakeAdapter(
        {
            "svc-a": [
                AdmissionDecision.DELAYED,
                AdmissionDecision.ACCEPTED,
            ]
        }
    )
    service = GatewayService(journal, FakeVerifier(), adapter)

    delayed = await service.handle(request(10), 1001)
    accepted = await service.handle(request(11), 1001)

    assert isinstance(delayed, ReserveResponse)
    assert delayed.decision == AdmissionDecision.DELAYED
    assert isinstance(accepted, ReserveResponse)
    assert accepted.decision == AdmissionDecision.ACCEPTED
    assert adapter.reserves == ["svc-a", "svc-a", "svc-b"]
    journal.close()


async def _partial_reserve_is_rolled_back(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    adapter = FakeAdapter({"svc-b": AdmissionDecision.REJECTED})
    service = GatewayService(journal, FakeVerifier(), adapter)
    response = await service.handle(request(), 1001)
    assert isinstance(response, ReserveResponse)
    assert response.decision == AdmissionDecision.REJECTED
    assert adapter.releases == [("svc-a", 40, 1)]
    assert all(item.reservation_id is None for item in response.results)
    released = await service.handle(ReleaseRequest(2, "cluster", 100, 3), 1001)
    assert isinstance(released, ReleaseResponse)
    assert len(released.results) == 1
    assert released.results[0].state == ReservationState.ALREADY_TERMINAL
    journal.close()


def test_resolve_failure_rolls_back_prior_service(tmp_path) -> None:
    asyncio.run(_resolve_failure_rolls_back_prior_service(tmp_path))


async def _resolve_failure_rolls_back_prior_service(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    adapter = FailingAdapter()
    service = GatewayService(journal, FakeVerifier(), adapter)
    response = await service.handle(request(), 1001)
    assert response.error_code == GatewayError.INTERNAL
    assert adapter.releases == [("svc-a", 40, 1)]
    journal.close()


def test_release_rejects_stale_runtime_without_calling_qpm(tmp_path) -> None:
    asyncio.run(_release_rejects_stale_runtime_without_calling_qpm(tmp_path))


async def _release_rejects_stale_runtime_without_calling_qpm(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    adapter = FakeAdapter()
    service = GatewayService(journal, FakeVerifier(), adapter)
    assert isinstance(await service.handle(request(), 1001), ReserveResponse)
    adapter.generations["svc-a"] = ("replacement", 2)
    response = await service.handle(ReleaseRequest(2, "cluster", 100, 3), 1001)
    states = {item.service_id: item.state for item in response.results}
    assert states["svc-a"] == ReservationState.STALE_RUNTIME
    assert states["svc-b"] == ReservationState.RELEASED
    assert [item[0] for item in adapter.releases] == ["svc-b"]
    journal.close()


def test_release_failure_remains_nonterminal(tmp_path) -> None:
    asyncio.run(_release_failure_remains_nonterminal(tmp_path))


async def _release_failure_remains_nonterminal(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    adapter = ReleaseFailingAdapter()
    service = GatewayService(journal, FakeVerifier(), adapter)
    assert isinstance(await service.handle(request(), 1001), ReserveResponse)

    response = await service.handle(ReleaseRequest(2, "cluster", 100, 3), 1001)

    assert isinstance(response, ReleaseResponse)
    assert {item.state for item in response.results} == {
        ReservationState.QPM_FAILURE
    }
    status = journal.allocation_status("cluster", 100)
    assert status["state"] == "release-incomplete"
    journal.close()


def test_request_id_content_conflict_is_reported(tmp_path) -> None:
    asyncio.run(_request_id_content_conflict_is_reported(tmp_path))


async def _request_id_content_conflict_is_reported(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    service = GatewayService(journal, FakeVerifier(), FakeAdapter())
    assert isinstance(await service.handle(request(), 1001), ReserveResponse)
    changed = dataclasses.replace(request(), canonical_job_id=101)
    response = await service.handle(changed, 1001)
    assert response.error_code == GatewayError.REQUEST_CONFLICT
    journal.close()


def test_heterogeneous_components_extend_one_reservation_set(tmp_path) -> None:
    asyncio.run(_heterogeneous_components_extend_one_reservation_set(tmp_path))


async def _heterogeneous_components_extend_one_reservation_set(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    adapter = FakeAdapter()
    service = GatewayService(journal, FakeVerifier(), adapter)
    first_request = dataclasses.replace(
        request(),
        service_ids=("svc-a",),
        hetero_job_id=100,
        hetero_component=0,
    )
    second_request = dataclasses.replace(
        request(),
        service_ids=("svc-b",),
        hetero_job_id=101,
        hetero_component=1,
    )

    first = await service.handle(first_request, 1001)
    second = await service.handle(second_request, 1001)
    replay = await service.handle(first_request, 1001)

    assert isinstance(first, ReserveResponse)
    assert isinstance(second, ReserveResponse)
    assert [item.service_id for item in second.results] == ["svc-a", "svc-b"]
    assert replay == dataclasses.replace(second, request_id=first.request_id)
    assert adapter.reserves == ["svc-a", "svc-b"]
    journal.close()


def test_failed_heterogeneous_extension_preserves_existing_set(tmp_path) -> None:
    asyncio.run(_failed_heterogeneous_extension_preserves_existing_set(tmp_path))


async def _failed_heterogeneous_extension_preserves_existing_set(tmp_path) -> None:
    journal = Journal(tmp_path / "state.db")
    adapter = FakeAdapter()
    service = GatewayService(journal, FakeVerifier(), adapter)
    first_request = dataclasses.replace(
        request(),
        service_ids=("svc-a",),
        hetero_job_id=100,
        hetero_component=0,
    )
    assert isinstance(await service.handle(first_request, 1001), ReserveResponse)
    adapter.decisions["svc-b"] = AdmissionDecision.REJECTED
    extension = dataclasses.replace(
        request(),
        service_ids=("svc-b",),
        hetero_job_id=101,
        hetero_component=1,
    )

    rejected = await service.handle(extension, 1001)
    retrieved = await service.handle(
        dataclasses.replace(first_request, request_id=9, workload=None),
        1001,
    )

    assert rejected.decision == AdmissionDecision.REJECTED
    assert isinstance(retrieved, ReserveResponse)
    assert [item.service_id for item in retrieved.results] == ["svc-a"]
    journal.close()
