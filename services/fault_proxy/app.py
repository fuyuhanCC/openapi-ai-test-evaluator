"""FastAPI application for deterministic response-side fault injection."""

from __future__ import annotations

import os
from collections.abc import Iterable
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from threading import Lock
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException, Path, Request
from fastapi.responses import JSONResponse, Response

from openapi_ai_test_evaluator.domain.execution import HttpMethod
from openapi_ai_test_evaluator.domain.fault import (
    FAULT_ID_RESPONSE_HEADER,
    FaultDefinition,
    FaultProxyMode,
    FaultProxyState,
)
from services.fault_proxy.catalog import load_fault_catalog
from services.fault_proxy.mutations import (
    FaultApplication,
    FaultApplicationReason,
    FaultRequestContext,
    FaultResponse,
    apply_response_fault,
)

DEFAULT_UPSTREAM_URL = "http://demo-items:8000"
DEFAULT_MAX_RESPONSE_BYTES = 1_048_576
FAULT_HEADER = FAULT_ID_RESPONSE_HEADER

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class FaultProxyController:
    """Own the fault catalog and the one active in-memory fault state."""

    def __init__(self, definitions: Iterable[FaultDefinition]) -> None:
        catalog: dict[str, FaultDefinition] = {}
        for definition in definitions:
            if definition.fault_id in catalog:
                raise ValueError(f"duplicate fault ID {definition.fault_id!r}")
            catalog[definition.fault_id] = definition
        self._catalog = catalog
        self._active: FaultDefinition | None = None
        self._trigger_count = 0
        self._lock = Lock()

    def activate(self, fault_id: str) -> FaultProxyState:
        with self._lock:
            try:
                self._active = self._catalog[fault_id]
            except KeyError as error:
                raise LookupError(fault_id) from error
            self._trigger_count = 0
            return self._state_unlocked()

    def reset(self) -> FaultProxyState:
        with self._lock:
            self._active = None
            self._trigger_count = 0
            return self._state_unlocked()

    def state(self) -> FaultProxyState:
        with self._lock:
            return self._state_unlocked()

    def apply(
        self,
        request: FaultRequestContext,
        response: FaultResponse,
    ) -> tuple[FaultApplication, str | None]:
        with self._lock:
            if self._active is None:
                return (
                    FaultApplication(
                        response=response,
                        triggered=False,
                        reason=FaultApplicationReason.NO_ACTIVE_FAULT,
                    ),
                    None,
                )
            fault_id = self._active.fault_id
            application = apply_response_fault(self._active, request, response)
            if application.triggered:
                self._trigger_count += 1
            return application, fault_id

    def _state_unlocked(self) -> FaultProxyState:
        if self._active is None:
            return FaultProxyState(
                mode=FaultProxyMode.PASS_THROUGH,
                configured_fault_id=None,
                trigger_count=0,
            )
        return FaultProxyState(
            mode=FaultProxyMode.ACTIVE,
            configured_fault_id=self._active.fault_id,
            trigger_count=self._trigger_count,
        )


def create_app(
    upstream_base_url: str | None = None,
    *,
    faults: Iterable[FaultDefinition] = (),
    transport: httpx.AsyncBaseTransport | None = None,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> FastAPI:
    """Create an isolated proxy application with its own fault state."""
    if max_response_bytes < 1:
        raise ValueError("max_response_bytes must be positive")
    base_url = upstream_base_url or os.getenv("OATE_FAULT_PROXY_UPSTREAM") or DEFAULT_UPSTREAM_URL
    normalized_base_url = f"{base_url.rstrip('/')}/"
    controller = FaultProxyController(faults)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with httpx.AsyncClient(
            base_url=normalized_base_url,
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        ) as upstream_client:
            application.state.upstream_client = upstream_client
            yield

    application = FastAPI(title="OATE Fault Proxy", lifespan=lifespan)
    application.state.fault_controller = controller

    @application.get("/__oate__/state", response_model=FaultProxyState)
    def get_state() -> FaultProxyState:
        return controller.state()

    @application.put("/__oate__/faults/{fault_id}", response_model=FaultProxyState)
    def activate_fault(
        fault_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$")],
    ) -> FaultProxyState:
        try:
            return controller.activate(fault_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="unknown fault ID") from error

    @application.delete("/__oate__/fault", response_model=FaultProxyState)
    def reset_fault() -> FaultProxyState:
        return controller.reset()

    @application.api_route(
        "/{proxy_path:path}",
        methods=[method.value for method in HttpMethod],
    )
    async def proxy_request(request: Request, proxy_path: str) -> Response:
        del proxy_path
        upstream_response = await _forward_request(
            application.state.upstream_client,
            request,
            max_response_bytes=max_response_bytes,
        )
        if isinstance(upstream_response, JSONResponse):
            return upstream_response

        request_context = FaultRequestContext(
            method=HttpMethod(request.method),
            path=request.url.path,
        )
        application_result, configured_fault_id = controller.apply(
            request_context,
            upstream_response,
        )
        fault_id = configured_fault_id if application_result.triggered else None
        return _build_downstream_response(application_result.response, fault_id=fault_id)

    return application


async def _forward_request(
    client: httpx.AsyncClient,
    request: Request,
    *,
    max_response_bytes: int,
) -> FaultResponse | JSONResponse:
    request_target = request.url.path.lstrip("/")
    if request.url.query:
        request_target = f"{request_target}?{request.url.query}"
    try:
        upstream_request = client.build_request(
            request.method,
            request_target,
            headers=_request_headers(request),
            content=await request.body(),
        )
        upstream_response = await client.send(upstream_request, stream=True)
        try:
            body = await _read_bounded_body(upstream_response, max_response_bytes)
            return FaultResponse(
                status_code=upstream_response.status_code,
                headers=_upstream_response_headers(upstream_response),
                body=body,
            )
        finally:
            await upstream_response.aclose()
    except UpstreamResponseTooLarge:
        return JSONResponse(status_code=502, content={"detail": "upstream response too large"})
    except httpx.RequestError:
        return JSONResponse(status_code=502, content={"detail": "upstream request failed"})


class UpstreamResponseTooLarge(RuntimeError):
    pass


async def _read_bounded_body(response: httpx.Response, maximum: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > maximum:
            raise UpstreamResponseTooLarge
        body.extend(chunk)
    return bytes(body)


def _request_headers(request: Request) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name.decode("latin-1"), value.decode("latin-1"))
        for name, value in request.headers.raw
        if name.decode("latin-1").casefold() not in {*_HOP_BY_HOP_HEADERS, "host", "content-length"}
    )


def _upstream_response_headers(response: httpx.Response) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name, value)
        for name, value in response.headers.multi_items()
        if name.casefold() not in {*_HOP_BY_HOP_HEADERS, "content-length", "content-encoding"}
    )


def _build_downstream_response(response: FaultResponse, *, fault_id: str | None) -> Response:
    headers = [
        (name, value)
        for name, value in response.headers
        if name.casefold() not in {"content-length", FAULT_HEADER}
    ]
    if fault_id is not None:
        headers.append((FAULT_HEADER, fault_id))
    if response.status_code >= 200 and response.status_code not in {204, 304}:
        headers.append(("content-length", str(len(response.body))))

    downstream = Response(content=response.body, status_code=response.status_code)
    downstream.raw_headers = [
        (name.encode("latin-1"), value.encode("latin-1")) for name, value in headers
    ]
    return downstream


def _environment_faults() -> list[FaultDefinition]:
    directory = os.getenv("OATE_FAULT_PROXY_FAULTS")
    return [] if directory is None else load_fault_catalog(FilePath(directory))


app = create_app(faults=_environment_faults())


__all__ = ["FaultProxyController", "app", "create_app"]
