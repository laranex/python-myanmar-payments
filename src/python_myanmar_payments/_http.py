"""Sends the gateways' HTTP requests with httpx, sync or async."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, TypeVar
from urllib.parse import urlencode

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


def _response(response: httpx.Response) -> GatewayResponse:
    return GatewayResponse(response.status_code, response.content.decode("utf-8", "replace"))


class SyncTransport:
    """Posts requests with an ``httpx.Client``, created on first use unless given."""

    def __init__(self, client: httpx.Client | None, timeout: float) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._lock = threading.Lock()

    def send(self, request: HttpRequest) -> GatewayResponse:
        try:
            response = self._http().post(
                request.url, content=request.body.encode(), headers=dict(request.headers)
            )
        except Exception as error:
            raise _unreachable(request, error) from error
        return _response(response)

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
    """Posts requests with an ``httpx.AsyncClient``, created on first use unless given."""

    def __init__(self, client: httpx.AsyncClient | None, timeout: float) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    async def send(self, request: HttpRequest) -> GatewayResponse:
        try:
            response = await self._http().post(
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
