"""Execute one frozen test-case batch against clean and faulty API states."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from pydantic import TypeAdapter, ValidationError

from openapi_ai_test_evaluator.domain.contracts import Identifier
from openapi_ai_test_evaluator.domain.execution import (
    FaultObservation,
    FaultTriggerStatus,
    RunResult,
)
from openapi_ai_test_evaluator.domain.fault import FaultProxyMode, FaultProxyState
from openapi_ai_test_evaluator.domain.openapi import OpenAPISpec
from openapi_ai_test_evaluator.domain.test_case import TestCaseBatch
from openapi_ai_test_evaluator.execution.case_batch_executor import execute_test_case_batch
from openapi_ai_test_evaluator.execution.plan_executor import validate_base_url

_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)


class BenchmarkControlError(RuntimeError):
    """The experiment could not establish or observe the requested API state."""


@dataclass(frozen=True, slots=True)
class FaultRun:
    fault_id: str
    result: RunResult


@dataclass(frozen=True, slots=True)
class SuiteExecution:
    """Raw clean and per-fault results for one frozen test-case batch."""

    suite_id: str
    repetition: int
    clean: RunResult
    faults: tuple[FaultRun, ...]


class BenchmarkControlClient:
    """Control SUT reset and fault-proxy state outside measured test traffic."""

    def __init__(
        self,
        proxy_control_url: str,
        sut_reset_url: str,
        *,
        timeout_ms: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._proxy_control_url = validate_base_url(proxy_control_url)
        self._sut_reset_url = _validate_control_url(sut_reset_url)
        self._client = httpx.Client(
            timeout=timeout_ms / 1000,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        )

    def __enter__(self) -> BenchmarkControlClient:
        return self

    def __exit__(self, *_: object) -> None:
        self._client.close()

    def reset_sut(self) -> None:
        response = self._request("POST", self._sut_reset_url)
        if not 200 <= response.status_code < 300:
            raise BenchmarkControlError(
                f"SUT reset returned unexpected status {response.status_code}"
            )

    def disable_fault(self) -> FaultProxyState:
        response = self._request("DELETE", f"{self._proxy_control_url}/__oate__/fault")
        state = _parse_proxy_state(response)
        if state.mode is not FaultProxyMode.PASS_THROUGH:
            raise BenchmarkControlError("fault proxy did not enter pass-through mode")
        return state

    def activate_fault(self, fault_id: str) -> FaultProxyState:
        response = self._request(
            "PUT",
            f"{self._proxy_control_url}/__oate__/faults/{fault_id}",
        )
        state = _parse_proxy_state(response)
        if (
            state.mode is not FaultProxyMode.ACTIVE
            or state.configured_fault_id != fault_id
            or state.trigger_count != 0
        ):
            raise BenchmarkControlError(f"fault proxy did not activate {fault_id!r} cleanly")
        return state

    def state(self) -> FaultProxyState:
        response = self._request("GET", f"{self._proxy_control_url}/__oate__/state")
        return _parse_proxy_state(response)

    def _request(self, method: str, url: str) -> httpx.Response:
        try:
            response = self._client.request(method, url)
            response.raise_for_status()
            return response
        except (httpx.RequestError, httpx.HTTPStatusError) as error:
            raise BenchmarkControlError(f"benchmark control request failed: {method}") from error


def execute_fault_suite(
    batch: TestCaseBatch,
    spec: OpenAPISpec,
    *,
    suite_id: str,
    repetition: int,
    runner_base_url: str,
    proxy_control_url: str,
    sut_reset_url: str,
    fault_ids: list[str],
    timeout_ms: int = 5000,
    allow_mutations: bool = False,
    execution_transport: httpx.BaseTransport | None = None,
    control_transport: httpx.BaseTransport | None = None,
) -> SuiteExecution:
    """Run one unchanged batch once clean and once for every configured fault."""
    if repetition < 1:
        raise ValueError("repetition must be positive")
    if len(fault_ids) != len(set(fault_ids)):
        raise ValueError("fault IDs must be unique within one suite execution")
    try:
        actual_suite_id = _IDENTIFIER_ADAPTER.validate_python(suite_id)
        actual_fault_ids = [_IDENTIFIER_ADAPTER.validate_python(fault_id) for fault_id in fault_ids]
    except ValidationError as error:
        raise ValueError("suite and fault IDs must be lowercase hyphenated identifiers") from error
    actual_runner_base_url = validate_base_url(runner_base_url)

    with BenchmarkControlClient(
        proxy_control_url,
        sut_reset_url,
        timeout_ms=timeout_ms,
        transport=control_transport,
    ) as control:
        control.disable_fault()
        control.reset_sut()
        clean = execute_test_case_batch(
            batch,
            spec,
            actual_runner_base_url,
            batch_name=actual_suite_id,
            timeout_ms=timeout_ms,
            run_id=f"{actual_suite_id}-r{repetition}-clean",
            allow_mutations=allow_mutations,
            httpx_transport=execution_transport,
        )

        fault_runs: list[FaultRun] = []
        try:
            for fault_id in actual_fault_ids:
                control.disable_fault()
                control.reset_sut()
                control.activate_fault(fault_id)
                raw_result = execute_test_case_batch(
                    batch,
                    spec,
                    actual_runner_base_url,
                    batch_name=actual_suite_id,
                    timeout_ms=timeout_ms,
                    run_id=f"{actual_suite_id}-r{repetition}-{fault_id}",
                    fault=FaultObservation(
                        configured_fault_id=fault_id,
                        trigger_status=FaultTriggerStatus.UNKNOWN,
                        trigger_count=0,
                    ),
                    allow_mutations=allow_mutations,
                    httpx_transport=execution_transport,
                )
                state = control.state()
                fault_runs.append(
                    FaultRun(
                        fault_id=fault_id,
                        result=_record_observed_fault(raw_result, fault_id, state),
                    )
                )
        finally:
            control.disable_fault()

    return SuiteExecution(
        suite_id=actual_suite_id,
        repetition=repetition,
        clean=clean,
        faults=tuple(fault_runs),
    )


def _record_observed_fault(
    result: RunResult,
    fault_id: str,
    state: FaultProxyState,
) -> RunResult:
    if state.mode is not FaultProxyMode.ACTIVE or state.configured_fault_id != fault_id:
        raise BenchmarkControlError(
            f"fault proxy state no longer matches configured fault {fault_id!r}"
        )
    trigger_status = (
        FaultTriggerStatus.TRIGGERED
        if state.trigger_count > 0
        else FaultTriggerStatus.NOT_TRIGGERED
    )
    document = result.model_dump(mode="python")
    document["fault"] = FaultObservation(
        configured_fault_id=fault_id,
        trigger_status=trigger_status,
        trigger_count=state.trigger_count,
    )
    return RunResult.model_validate(document)


def _parse_proxy_state(response: httpx.Response) -> FaultProxyState:
    try:
        return FaultProxyState.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise BenchmarkControlError("fault proxy returned an invalid state document") from error


def _validate_control_url(url: str) -> str:
    try:
        parsed = httpx.URL(url)
    except (TypeError, ValueError) as error:
        raise ValueError("SUT reset URL is invalid") from error
    if parsed.scheme not in {"http", "https"} or parsed.host is None:
        raise ValueError("SUT reset URL must be absolute HTTP(S)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("SUT reset URL cannot contain credentials, query, or fragment")
    return str(parsed)


__all__ = [
    "BenchmarkControlClient",
    "BenchmarkControlError",
    "FaultRun",
    "SuiteExecution",
    "execute_fault_suite",
]
