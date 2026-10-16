"""Sends the gateways' HTTP requests with httpx, sync or async."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, TypeVar
from urllib.parse import urlencode

import anyio
import httpx

from ._errors import ApiError
from ._json import LosslessObject, dumps, parse_object


@dataclass(frozen=True)
class HttpRequest:
    """A request a gateway sends. Gateways only send ``POST`` requests."""

    url: str
    body: str
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayResponse:
    """A gateway's response: its status and raw body text."""

    status: int
    body: str

    def successful(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> LosslessObject:
        """The body as a JSON object with exact numbers, or ``{}`` when it is not one."""
        return parse_object(self.body) or {}


def json_request(
    url: str, data: Mapping[str, Any], headers: Mapping[str, str] | None = None
) -> HttpRequest:
    return HttpRequest(
        url=url,
        body=dumps(data),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        },
    )


def form_request(
    url: str, data: Mapping[str, str], headers: Mapping[str, str] | None = None
) -> HttpRequest:
    return HttpRequest(
        url=url,
        body=urlencode(data),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            **(headers or {}),
        },
    )


def _unreachable(request: HttpRequest, error: Exception) -> ApiError:
    return ApiError(f"Could not reach {request.url}: {str(error) or type(error).__name__}")


def _timed_out(request: HttpRequest) -> ApiError:
    return ApiError(f"Could not reach {request.url}: timed out")


def _response(response: httpx.Response) -> GatewayResponse:
    return GatewayResponse(response.status_code, response.content.decode("utf-8", "replace"))


def _post(client: httpx.Client, request: HttpRequest) -> GatewayResponse:
    try:
        response = client.post(
            request.url, content=request.body.encode(), headers=dict(request.headers)
        )
    except Exception as error:
        raise _unreachable(request, error) from error
    return _response(response)


class SyncTransport:
    """Posts requests with an ``httpx.Client``, created on first use unless given.

    With the client it creates, ``timeout`` is one overall deadline per request
    (connecting, sending and reading the whole response), like the other SDKs. A
    client you pass keeps its own timeout.
    """

    def __init__(self, client: httpx.Client | None, timeout: float) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._lock = threading.Lock()

    def send(self, request: HttpRequest) -> GatewayResponse:
        client = self._http()
        if not self._owns_client:
            return _post(client, request)
        # httpx applies a timeout to each phase, so the request runs in a worker
        # thread and the caller stops waiting once the overall deadline passes.
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            return executor.submit(_post, client, request).result(timeout=self._timeout)
        except FutureTimeoutError:
            raise _timed_out(request) from None
        finally:
            executor.shutdown(wait=False)

    def _http(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(timeout=self._timeout)
            return self._client

    def close(self) -> None:
        """Closes the client this transport created; a client you passed stays open."""
        with self._lock:
            if self._owns_client and self._client is not None:
                self._client.close()
                self._client = None


class AsyncTransport:
    """Posts requests with an ``httpx.AsyncClient``, created on first use unless given.

    With the client it creates, ``timeout`` is one overall deadline per request
    (connecting, sending and reading the whole response). A client you pass keeps
    its own timeout.
    """

    def __init__(self, client: httpx.AsyncClient | None, timeout: float) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    async def send(self, request: HttpRequest) -> GatewayResponse:
        client = self._http()
        if not self._owns_client:
            return await self._post(client, request)
        try:
            with anyio.fail_after(self._timeout):
                return await self._post(client, request)
        except TimeoutError:
            raise _timed_out(request) from None

    @staticmethod
    async def _post(client: httpx.AsyncClient, request: HttpRequest) -> GatewayResponse:
        try:
            response = await client.post(
                request.url, content=request.body.encode(), headers=dict(request.headers)
            )
        except Exception as error:
            raise _unreachable(request, error) from error
        return _response(response)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        """Closes the client this transport created; a client you passed stays open."""
        if self._owns_client and self._client is not None:
            client, self._client = self._client, None
            await client.aclose()


_S = TypeVar("_S", bound="SyncGateway")
_A = TypeVar("_A", bound="AsyncGateway")


class SyncGateway:
    """A gateway that calls its API with a synchronous ``httpx.Client``.

    Pass ``http_client`` to share a client (proxies, tracing, test transports);
    otherwise one is created on first use with the config's ``timeout_seconds``
    and closed by :meth:`close` or a ``with`` block. A client you pass keeps its
    own timeout.
    """

    _transport: SyncTransport

    def _init_transport(self, http_client: httpx.Client | None, timeout: float) -> None:
        self._transport = SyncTransport(http_client, timeout)

    def close(self) -> None:
        """Closes the HTTP client the gateway created. A client you passed stays open."""
        self._transport.close()

    def __enter__(self: _S) -> _S:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class AsyncGateway:
    """A gateway that calls its API with an ``httpx.AsyncClient``.

    Pass ``http_client`` to share a client; otherwise one is created on first use
    with the config's ``timeout_seconds`` and closed by :meth:`aclose` or an
    ``async with`` block. A client you pass keeps its own timeout.
    """

    _transport: AsyncTransport

    def _init_transport(self, http_client: httpx.AsyncClient | None, timeout: float) -> None:
        self._transport = AsyncTransport(http_client, timeout)

    async def aclose(self) -> None:
        """Closes the HTTP client the gateway created. A client you passed stays open."""
        await self._transport.aclose()

    async def __aenter__(self: _A) -> _A:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()
