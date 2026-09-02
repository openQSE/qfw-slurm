"""Allocation-level reserve and release transaction coordinator."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
from typing import Any
from weakref import WeakValueDictionary

from .defw_client import QFwAdapter, QFwAdapterError, QPMBinding
from .journal import Journal, JournalError, ReservationRecord, RequestConflict
from .protocol import (
    AdmissionDecision,
    ErrorResponse,
    EvaluateRequest,
    EvaluateResponse,
    GatewayError,
    ReleaseRequest,
    ReleaseResponse,
    ReleaseResult,
    ReservationState,
    ReserveRequest,
    ReserveResponse,
    ServiceResult,
    bounded_diagnostic,
    response_from_dict,
    response_to_dict,
)
from .slurm_verifier import SlurmVerificationError, SlurmVerifier


def _fingerprint(value: Any) -> str:
    document = dataclasses.asdict(value) if dataclasses.is_dataclass(value) else value
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), default=int
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _allocation_fingerprint(request: ReserveRequest) -> str:
    return _fingerprint(
        {
            "cluster_name": request.cluster_name,
            "canonical_job_id": request.canonical_job_id,
            "job_uid": request.job_uid,
            "job_gid": request.job_gid,
            "workload": dataclasses.asdict(request.workload)
            if request.workload is not None
            else None,
            "service_ids": sorted(request.service_ids),
        }
    )


def _reservation_fingerprint(request: ReserveRequest) -> str:
    return _fingerprint(
        {
            "cluster_name": request.cluster_name,
            "canonical_job_id": request.canonical_job_id,
            "job_uid": request.job_uid,
            "job_gid": request.job_gid,
            "workload": dataclasses.asdict(request.workload)
            if request.workload is not None
            else None,
        }
    )


def _operation_scope(
    request: EvaluateRequest | ReserveRequest | ReleaseRequest,
) -> str:
    if isinstance(request, ReserveRequest) and request.hetero_job_id is not None:
        return f"heterogeneous:{request.hetero_job_id}:{request.hetero_component}"
    return "allocation"


def _error(code: GatewayError, request_id: int, detail: object) -> ErrorResponse:
    return ErrorResponse(code, request_id, bounded_diagnostic(detail))


class GatewayService:
    """Serialize side effects per Slurm allocation and journal every outcome."""

    def __init__(
        self, journal: Journal, verifier: SlurmVerifier, adapter: QFwAdapter
    ):
        self.journal = journal
        self.verifier = verifier
        self.adapter = adapter
        self._locks: WeakValueDictionary[tuple[str, int], asyncio.Lock] = (
            WeakValueDictionary()
        )

    def _allocation_lock(self, key: tuple[str, int]) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def handle(
        self,
        request: EvaluateRequest | ReserveRequest | ReleaseRequest,
        sender_uid: int,
    ) -> EvaluateResponse | ReserveResponse | ReleaseResponse | ErrorResponse:
        key = (request.cluster_name, request.canonical_job_id)
        async with self._allocation_lock(key):
            if isinstance(request, EvaluateRequest):
                return await self._evaluate(request, sender_uid)
            if isinstance(request, ReserveRequest):
                return await self._reserve(request, sender_uid)
            return await self._release(request, sender_uid)

    async def _evaluate(
        self, request: EvaluateRequest, sender_uid: int
    ) -> EvaluateResponse | ErrorResponse:
        try:
            job = await asyncio.to_thread(
                self.verifier.verify_reserve, request, sender_uid
            )
            fingerprint = _fingerprint(request)
            scope = _operation_scope(request)
            prior = self.journal.begin_operation(
                sender_uid,
                "evaluate",
                request.request_id,
                fingerprint,
                scope,
            )
            if prior is not None:
                if prior.state != "complete" or prior.response is None:
                    raise JournalError("evaluate operation requires recovery")
                response = response_from_dict(prior.response)
                if not isinstance(response, EvaluateResponse):
                    raise JournalError("stored evaluate response has wrong type")
                return response
            results = []
            for service_id in sorted(request.service_ids):
                binding = await asyncio.to_thread(self.adapter.resolve, service_id)
                result = await asyncio.to_thread(
                    self.adapter.evaluate, binding, request, job
                )
                if result.reservation_id is not None:
                    raise QFwAdapterError(
                        f"QPM {service_id!r} evaluation returned a reservation"
                    )
                results.append(result)
            decisions = {item.decision for item in results}
            if AdmissionDecision.REJECTED in decisions:
                decision = AdmissionDecision.REJECTED
            elif AdmissionDecision.DELAYED in decisions:
                decision = AdmissionDecision.DELAYED
            else:
                decision = AdmissionDecision.ACCEPTED
            response = EvaluateResponse(
                request.request_id, decision, tuple(results)
            )
            state = (
                "retryable"
                if decision == AdmissionDecision.DELAYED
                else "complete"
            )
            self.journal.complete_operation(
                sender_uid,
                "evaluate",
                request.request_id,
                response_to_dict(response),
                scope,
                state,
            )
            return response
        except RequestConflict as error:
            return _error(GatewayError.REQUEST_CONFLICT, request.request_id, error)
        except SlurmVerificationError as error:
            return _error(GatewayError.UNAUTHORIZED, request.request_id, error)
        except QFwAdapterError as error:
            return _error(GatewayError.QPM, request.request_id, error)
        except JournalError as error:
            return _error(GatewayError.INTERNAL, request.request_id, error)
        except Exception as error:
            return _error(
                GatewayError.INTERNAL,
                request.request_id,
                f"unexpected evaluate failure: {error}",
            )

    async def _reserve(
        self, request: ReserveRequest, sender_uid: int
    ) -> ReserveResponse | ErrorResponse:
        try:
            job = await asyncio.to_thread(
                self.verifier.verify_reserve, request, sender_uid
            )
            if request.workload is None:
                existing = self.journal.accepted_allocation(
                    request.cluster_name,
                    request.canonical_job_id,
                    request.job_uid,
                    request.job_gid,
                    request.service_ids,
                )
                if existing is None:
                    return ErrorResponse(
                        GatewayError.INVALID_REQUEST,
                        request.request_id,
                        "no existing reservation set and no workload envelope",
                    )
                response = response_from_dict(existing)
                if not isinstance(response, ReserveResponse):
                    raise JournalError("stored allocation response has wrong type")
                return dataclasses.replace(response, request_id=request.request_id)
            fingerprint = _fingerprint(request)
            scope = _operation_scope(request)
            prior = self.journal.begin_operation(
                sender_uid,
                "reserve",
                request.request_id,
                fingerprint,
                scope,
            )
            if prior is not None:
                if prior.state != "complete" or prior.response is None:
                    raise JournalError("reserve operation requires recovery")
                response = response_from_dict(prior.response)
                if not isinstance(response, ReserveResponse):
                    raise JournalError("stored reserve response has wrong type")
                if response.decision == AdmissionDecision.ACCEPTED:
                    current = self.journal.accepted_allocation(
                        request.cluster_name,
                        request.canonical_job_id,
                        request.job_uid,
                        request.job_gid,
                        request.service_ids,
                    )
                    if current is not None:
                        response = response_from_dict(current)
                        if not isinstance(response, ReserveResponse):
                            raise JournalError(
                                "stored allocation response has wrong type"
                            )
                        response = dataclasses.replace(
                            response, request_id=request.request_id
                        )
                return response
            plan = self.journal.begin_allocation(
                request.cluster_name,
                request.canonical_job_id,
                request.job_uid,
                request.job_gid,
                request.service_ids,
                _allocation_fingerprint(request),
                _reservation_fingerprint(request),
            )
            if not plan.missing_services:
                response = response_from_dict(plan.existing_response)
                if not isinstance(response, ReserveResponse):
                    raise JournalError("stored allocation response has wrong type")
                response = dataclasses.replace(
                    response, request_id=request.request_id
                )
                self._complete(
                    "reserve",
                    sender_uid,
                    request.request_id,
                    response,
                    scope,
                )
                return response
            try:
                response = await self._reserve_services(
                    request,
                    job,
                    plan.missing_services,
                    _reservation_fingerprint(request),
                )
            except Exception:
                if plan.is_extension:
                    self.journal.restore_accepted_allocation(
                        request.cluster_name,
                        request.canonical_job_id,
                        plan.existing_services,
                        plan.existing_response,
                    )
                raise
            if plan.is_extension:
                if response.decision == AdmissionDecision.ACCEPTED:
                    previous = response_from_dict(plan.existing_response)
                    if not isinstance(previous, ReserveResponse):
                        raise JournalError(
                            "stored allocation response has wrong type"
                        )
                    response = ReserveResponse(
                        request.request_id,
                        AdmissionDecision.ACCEPTED,
                        tuple(
                            sorted(
                                (*previous.results, *response.results),
                                key=lambda item: item.service_id,
                            )
                        ),
                    )
                else:
                    self.journal.restore_accepted_allocation(
                        request.cluster_name,
                        request.canonical_job_id,
                        plan.existing_services,
                        plan.existing_response,
                    )
            state = {
                AdmissionDecision.ACCEPTED: "accepted",
                AdmissionDecision.DELAYED: "delayed",
                AdmissionDecision.REJECTED: "rejected",
            }[response.decision]
            encoded = response_to_dict(response)
            if not plan.is_extension or state == "accepted":
                self.journal.complete_allocation(
                    request.cluster_name,
                    request.canonical_job_id,
                    state,
                    encoded,
                )
            self.journal.complete_operation(
                sender_uid,
                "reserve",
                request.request_id,
                encoded,
                scope,
            )
            return response
        except RequestConflict as error:
            return _error(GatewayError.REQUEST_CONFLICT, request.request_id, error)
        except SlurmVerificationError as error:
            return _error(GatewayError.UNAUTHORIZED, request.request_id, error)
        except QFwAdapterError as error:
            return _error(GatewayError.QPM, request.request_id, error)
        except JournalError as error:
            return _error(GatewayError.INTERNAL, request.request_id, error)
        except Exception as error:
            return _error(
                GatewayError.INTERNAL,
                request.request_id,
                f"unexpected reserve failure: {error}",
            )

    async def _reserve_services(
        self,
        request,
        job,
        service_ids: tuple[str, ...],
        reservation_fingerprint: str,
    ) -> ReserveResponse:
        accepted: list[tuple[QPMBinding, ServiceResult]] = []
        results: list[ServiceResult] = []
        for service_id in sorted(service_ids):
            binding = None
            result = None
            try:
                binding = await asyncio.to_thread(self.adapter.resolve, service_id)
                self.journal.record_reservation(
                    request.cluster_name,
                    request.canonical_job_id,
                    ReservationRecord(
                        service_id,
                        None,
                        binding.runtime_id,
                        binding.generation,
                        "pending",
                        reservation_fingerprint,
                    ),
                )
                result = await asyncio.to_thread(
                    self.adapter.reserve, binding, request, job
                )
                record = ReservationRecord(
                    service_id=service_id,
                    reservation_id=result.reservation_id,
                    qpm_runtime_id=binding.runtime_id,
                    qpm_generation=binding.generation,
                    state=result.decision.name.lower(),
                    request_fingerprint=reservation_fingerprint,
                )
                self.journal.record_reservation(
                    request.cluster_name, request.canonical_job_id, record
                )
            except Exception as error:
                rollback = list(accepted)
                if (
                    binding is not None
                    and result is not None
                    and result.decision == AdmissionDecision.ACCEPTED
                ):
                    rollback.append((binding, result))
                try:
                    await self._rollback(request, rollback)
                except Exception as rollback_error:
                    raise JournalError(
                        f"reserve failed and rollback was incomplete: {rollback_error}"
                    ) from error
                raise
            results.append(result)
            if result.decision == AdmissionDecision.ACCEPTED:
                accepted.append((binding, result))
                continue
            await self._rollback(request, accepted)
            final = [
                dataclasses.replace(
                    item,
                    decision=result.decision,
                    reservation_id=None,
                    reason_code=result.reason_code,
                    diagnostic="rolled back after allocation-level failure",
                )
                for _binding, item in accepted
            ]
            final.append(result)
            return ReserveResponse(request.request_id, result.decision, tuple(final))
        return ReserveResponse(
            request.request_id, AdmissionDecision.ACCEPTED, tuple(results)
        )

    async def _rollback(
        self,
        request: ReserveRequest,
        accepted: list[tuple[QPMBinding, ServiceResult]],
    ) -> None:
        failures = []
        try:
            self.journal.set_allocation_state(
                request.cluster_name, request.canonical_job_id, "rolling-back"
            )
        except Exception as error:
            failures.append(str(error))
        for binding, result in reversed(accepted):
            try:
                await asyncio.to_thread(
                    self.adapter.release,
                    binding,
                    result.reservation_id,
                    1,
                )
                state = "rolled-back"
                diagnostic = None
            except Exception as error:
                state = "rollback-failed"
                diagnostic = bounded_diagnostic(error)
                failures.append(diagnostic)
            try:
                self.journal.record_reservation(
                    request.cluster_name,
                    request.canonical_job_id,
                    ReservationRecord(
                        binding.service_id,
                        result.reservation_id,
                        binding.runtime_id,
                        binding.generation,
                        state,
                        _reservation_fingerprint(request),
                    ),
                    diagnostic,
                )
            except Exception as error:
                failures.append(str(error))
        if failures:
            raise JournalError("allocation rollback was incomplete")

    async def _release(
        self, request: ReleaseRequest, sender_uid: int
    ) -> ReleaseResponse | ErrorResponse:
        try:
            fingerprint = _fingerprint(request)
            scope = _operation_scope(request)
            prior = self.journal.begin_operation(
                sender_uid,
                "release",
                request.request_id,
                fingerprint,
                scope,
            )
            if prior is not None:
                if prior.state != "complete" or prior.response is None:
                    raise JournalError("release operation requires recovery")
                response = response_from_dict(prior.response)
                if not isinstance(response, ReleaseResponse):
                    raise JournalError("stored release response has wrong type")
                return response
            status = self.journal.allocation_status(
                request.cluster_name, request.canonical_job_id
            )
            expected_uid = status.get("job_uid")
            await asyncio.to_thread(
                self.verifier.verify_release,
                request,
                sender_uid,
                expected_uid,
            )
            records = self.journal.reservations(
                request.cluster_name, request.canonical_job_id
            )
            results = []
            for record in records:
                if record.reservation_id is None:
                    continue
                results.append(await self._release_one(request, record))
            response = ReleaseResponse(request.request_id, tuple(results))
            encoded = response_to_dict(response)
            unresolved = {
                ReservationState.AUTHORIZATION_FAILURE,
                ReservationState.QPM_FAILURE,
                ReservationState.GATEWAY_FAILURE,
            }
            allocation_state = (
                "release-incomplete"
                if any(item.state in unresolved for item in results)
                else "released"
            )
            self.journal.complete_allocation(
                request.cluster_name,
                request.canonical_job_id,
                allocation_state,
                encoded,
            )
            self.journal.complete_operation(
                sender_uid,
                "release",
                request.request_id,
                encoded,
                scope,
            )
            return response
        except RequestConflict as error:
            return _error(GatewayError.REQUEST_CONFLICT, request.request_id, error)
        except SlurmVerificationError as error:
            return _error(GatewayError.UNAUTHORIZED, request.request_id, error)
        except JournalError as error:
            return _error(GatewayError.INTERNAL, request.request_id, error)
        except Exception as error:
            return _error(
                GatewayError.INTERNAL,
                request.request_id,
                f"unexpected release failure: {error}",
            )

    async def _release_one(
        self, request: ReleaseRequest, record: ReservationRecord
    ) -> ReleaseResult:
        terminal = {"released", "rolled-back", "delayed", "rejected"}
        if record.state in terminal or record.reservation_id is None:
            return ReleaseResult(
                record.service_id,
                record.reservation_id or 0,
                ReservationState.ALREADY_TERMINAL,
            )
        try:
            binding = await asyncio.to_thread(self.adapter.resolve, record.service_id)
            if (
                binding.runtime_id != record.qpm_runtime_id
                or binding.generation != record.qpm_generation
            ):
                state = ReservationState.STALE_RUNTIME
                diagnostic = "QPM incarnation differs from reservation journal"
            else:
                raw = await asyncio.to_thread(
                    self.adapter.release,
                    binding,
                    record.reservation_id,
                    request.reason,
                )
                status = str(raw.get("status", "")).lower()
                if status not in {"accepted", "released", "not-found"}:
                    raise QFwAdapterError(
                        f"QPM returned release status {status!r}"
                    )
                state = (
                    ReservationState.NOT_FOUND
                    if status == "not-found"
                    else ReservationState.RELEASED
                )
                diagnostic = None
        except Exception as error:
            state = ReservationState.QPM_FAILURE
            diagnostic = bounded_diagnostic(error)
        journal_state = {
            ReservationState.RELEASED: "released",
            ReservationState.NOT_FOUND: "released",
            ReservationState.STALE_RUNTIME: "stale-runtime",
            ReservationState.QPM_FAILURE: "release-failed",
        }[state]
        self.journal.record_reservation(
            request.cluster_name,
            request.canonical_job_id,
            dataclasses.replace(record, state=journal_state),
            diagnostic,
        )
        return ReleaseResult(
            record.service_id,
            record.reservation_id,
            state,
            GatewayError.QPM if state == ReservationState.QPM_FAILURE else None,
            diagnostic,
        )

    async def retry_release(self, request: ReleaseRequest) -> ReleaseResponse:
        """Retry unresolved rows from a protected local admin command."""

        key = (request.cluster_name, request.canonical_job_id)
        async with self._allocation_lock(key):
            records = self.journal.reservations(
                request.cluster_name, request.canonical_job_id
            )
            results = [
                await self._release_one(request, record)
                for record in records
                if record.reservation_id is not None
            ]
            response = ReleaseResponse(request.request_id, tuple(results))
            unresolved = {
                ReservationState.STALE_RUNTIME,
                ReservationState.QPM_FAILURE,
                ReservationState.GATEWAY_FAILURE,
            }
            state = (
                "release-incomplete"
                if any(item.state in unresolved for item in results)
                else "released"
            )
            self.journal.complete_allocation(
                request.cluster_name,
                request.canonical_job_id,
                state,
                response_to_dict(response),
            )
            return response

    def _complete(
        self, operation, sender_uid, request_id, response, scope="allocation"
    ) -> None:
        self.journal.complete_operation(
            sender_uid,
            operation,
            request_id,
            response_to_dict(response),
            scope,
        )
