from __future__ import annotations

import dataclasses
import asyncio

from qfw_slurm_gateway.defw_client import QPMBinding
from qfw_slurm_gateway.journal import Journal
from qfw_slurm_gateway.protocol import (
    AdmissionDecision,
    GatewayError,
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


class FakeAdapter:
    def __init__(self, decisions=None):
        self.decisions = decisions or {}
        self.reserves = []
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


def test_partial_reserve_is_rolled_back(tmp_path) -> None:
    asyncio.run(_partial_reserve_is_rolled_back(tmp_path))


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
