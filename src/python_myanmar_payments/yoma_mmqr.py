"""Yoma Bank MMQR: QR payments, status checks and callbacks."""

from __future__ import annotations

import asyncio
import base64
import inspect
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, ClassVar, cast

import httpx

from ._amount import AmountInput, to_amount
from ._cache import AsyncTokenCache, MemoryTokenCache, TokenCache
from ._callback import CallbackRequest, lossless_body
from ._errors import ApiError, SignatureVerificationError
from ._http import (
    AsyncGateway,
    GatewayResponse,
    HttpRequest,
    SyncGateway,
    form_request,
    json_request,
)
from ._json import LosslessObject, to_plain_object
from ._results import PaymentCallback, PaymentStatusResult, QrPayment
from ._status import PaymentStatus, resolve_status
from ._support import (
    EnvSource,
    config_of,
    default_env,
    env_first,
    hmac_sha256_hex,
    optional_setting,
    require_seconds,
    require_setting,
    safe_equal,
    sha256_hex,
    trim_url,
    utc_now,
)
from ._validate import AmountRule, Validator
from ._values import get, is_nested, optional, trimmed

__all__ = [
    "AsyncYomaMmqr",
    "YomaMmqr",
    "YomaMmqrConfig",
    "YomaMmqrPaymentData",
]


class YomaMmqrConfig:
    """Yoma MMQR credentials and endpoints.

    Every setting except the webhook secret and the URL override is required; a
    missing one raises a :class:`~python_myanmar_payments.ConfigurationError`.
    The URL defaults to production.
    """

    PRODUCTION_URL: ClassVar[str] = "https://paymenthubapi.yomabank.com"

    merchant_id: str
    """The merchant ID Yoma issued."""
    client_id: str
    """The OAuth client ID."""
    client_secret: str
    """The OAuth client secret."""
    webhook_hash_key: str
    """The hash key Yoma issued for verifying callbacks."""
    api_version: str
    """The ``{version}`` segment of the API paths, e.g. ``v1rc``."""
    timeout_seconds: int
    """Seconds before the default HTTP client gives up on a request."""
    webhook_secret: str | None
    """The secret you shared with Yoma; when set, callbacks must carry it in
    ``X-Webhook-Secret``."""
    base_url: str
    """The base URL in use."""

    def __init__(
        self,
        *,
        merchant_id: str = "",
        client_id: str = "",
        client_secret: str = "",
        webhook_hash_key: str = "",
        api_version: str = "",
        timeout_seconds: int | str | None = None,
        webhook_secret: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.merchant_id = require_setting("yoma_mmqr", "merchant_id", merchant_id)
        self.client_id = require_setting("yoma_mmqr", "client_id", client_id)
        self.client_secret = require_setting("yoma_mmqr", "client_secret", client_secret)
        self.webhook_hash_key = require_setting("yoma_mmqr", "webhook_hashkey", webhook_hash_key)
        self.api_version = require_setting("yoma_mmqr", "api_version", api_version)
        self.timeout_seconds = require_seconds("yoma_mmqr", "timeout_in_seconds", timeout_seconds)
        self.webhook_secret = optional_setting(webhook_secret)
        self.base_url = trim_url(optional_setting(base_url) or self.PRODUCTION_URL)

    @classmethod
    def from_env(cls, env: EnvSource | None = None) -> YomaMmqrConfig:
        """Reads the ``YOMA_MMQR_*`` environment variables.

        ``YOMA_MMQR_MERCHANT_ID``, ``YOMA_MMQR_CLIENT_ID``,
        ``YOMA_MMQR_CLIENT_SECRET``, ``YOMA_MMQR_WEBHOOK_HASHKEY``,
        ``YOMA_MMQR_API_VERSION``, ``MYANMAR_PAYMENTS_HTTP_TIMEOUT``,
        ``YOMA_MMQR_WEBHOOK_SECRET`` and ``YOMA_MMQR_BASE_URL``. Defaults to
        ``os.environ``.
        """
        env = default_env() if env is None else env
        return cls(
            merchant_id=env_first(env, "YOMA_MMQR_MERCHANT_ID"),
            client_id=env_first(env, "YOMA_MMQR_CLIENT_ID"),
            client_secret=env_first(env, "YOMA_MMQR_CLIENT_SECRET"),
            webhook_hash_key=env_first(env, "YOMA_MMQR_WEBHOOK_HASHKEY"),
            api_version=env_first(env, "YOMA_MMQR_API_VERSION"),
            timeout_seconds=env_first(env, "MYANMAR_PAYMENTS_HTTP_TIMEOUT"),
            webhook_secret=env_first(env, "YOMA_MMQR_WEBHOOK_SECRET"),
            base_url=env_first(env, "YOMA_MMQR_BASE_URL"),
        )

    def __repr__(self) -> str:
        return f"YomaMmqrConfig(merchant_id={self.merchant_id!r})"


@dataclass(kw_only=True)
class YomaMmqrPaymentData:
    """A Yoma MMQR order.

    Yoma accepts each order number once; renew an expired QR with ``renew_qr()``.
    Yoma documents no decimals, so amounts are whole kyat.
    """

    order_id: str
    """Your unique order number, at most 20 characters."""
    amount: AmountInput
    """The amount in whole kyat, e.g. ``Amount.kyat(1000)`` or ``1000``."""
    description: str
    """Shown on the payment slip, at most 50 characters."""


_STATUSES: Mapping[str, PaymentStatus] = {
    "SUCCESS": PaymentStatus.SUCCESSFUL,
    "PENDING": PaymentStatus.PENDING,
    "FAILED": PaymentStatus.FAILED,
    "FAIL": PaymentStatus.FAILED,
}

_LEADING_INT = re.compile(r"\s*([+-]?[0-9]+)")


@dataclass(frozen=True)
class _Call:
    path: str
    url: str
    data: Mapping[str, Any]
    allowed_errors: Sequence[str] = ()

    def request(self, token: str) -> HttpRequest:
        return json_request(self.url, self.data, {"Authorization": f"Bearer {token}"})


class _YomaMmqrBase:
    """What the sync and async Yoma clients share: requests, parsing and callbacks."""

    QR_LIFETIME_SECONDS: ClassVar[int] = 120
    """How long a generated QR stays payable, in seconds."""

    config: YomaMmqrConfig
    """The configuration in use."""

    def __init__(self, config: YomaMmqrConfig | Mapping[str, Any]) -> None:
        self.config = config_of(YomaMmqrConfig, config)

    @staticmethod
    def validate(data: YomaMmqrPaymentData) -> None:
        """Checks the order against Yoma's documented limits.

        Raises an :class:`~python_myanmar_payments.InvalidPaymentDataError`.
        """
        (
            Validator()
            .required("order_id", data.order_id)
            .max("order_id", data.order_id, 20)
            .amount("amount", data.amount, AmountRule("Yoma MMQR", 0))
            .required("description", data.description)
            .max("description", data.description, 50)
            .validate()
        )

    def handle_callback(self, request: CallbackRequest) -> PaymentCallback:
        """Verifies Yoma's payment callback.

        The hash is HMAC-SHA256 of ``orderNumber=..&status=..`` keyed with the
        order number followed by the webhook hash key.
        """
        payload = lossless_body(request)

        if self.config.webhook_secret is not None and not safe_equal(
            self.config.webhook_secret, request.header("X-Webhook-Secret") or ""
        ):
            raise SignatureVerificationError(
                "Yoma MMQR callback has a missing or wrong X-Webhook-Secret header.",
                to_plain_object(payload),
            )

        order_number = get(payload, "orderNumber")
        status = trimmed(payload, "status")
        expected = hmac_sha256_hex(
            order_number + self.config.webhook_hash_key,
            f"orderNumber={order_number}&status={status}",
        )
        if (
            order_number == ""
            or is_nested(payload.get("status"))
            or not safe_equal(expected, get(payload, "hashValue").lower())
        ):
            raise SignatureVerificationError(
                "Yoma MMQR callback hash verification failed.", to_plain_object(payload)
            )

        return PaymentCallback(
            order_id=order_number,
            status=resolve_status(_STATUSES, status.upper()),
            gateway_status=status,
            raw=to_plain_object(payload),
        )

    # --- Requests and results shared by both clients ------------------------------

    def _api(self, path: str, data: Mapping[str, Any], allowed: Sequence[str] = ()) -> _Call:
        url = f"{self.config.base_url}/payment-gateway/{self.config.api_version}/api/{path}"
        return _Call(path, url, data, allowed)

    def _checkout_call(self, data: YomaMmqrPaymentData) -> _Call:
        self.validate(data)
        return self._api(
            "payment/checkout",
            {
                "merchantId": self.config.merchant_id,
                "orderNumber": data.order_id,
                "amount": str(to_amount(data.amount)),
                "description": data.description,
            },
        )

    @staticmethod
    def _check_checkout(body: LosslessObject) -> None:
        if body.get("checkOutStatus") is not True:
            raise ApiError("Yoma MMQR did not confirm the checkout.", raw=to_plain_object(body))

    def _renew_call(self, order_id: str) -> _Call:
        return self._api(
            "qr/generate", {"merchantId": self.config.merchant_id, "orderNumber": order_id}
        )

    def _qr_payment(self, order_id: str, body: LosslessObject) -> QrPayment:
        qr_string = get(body, "qrString")
        reference = get(body, "refLabel")
        if qr_string == "" or reference == "":
            raise ApiError("Yoma MMQR did not return a QR.", raw=to_plain_object(body))
        return QrPayment(
            order_id=order_id,
            qr_image=qr_string,
            expires_at=utc_now() + timedelta(seconds=self.QR_LIFETIME_SECONDS),
            reference=reference,
            raw=to_plain_object(body),
        )

    def _status_call(self, reference: str) -> _Call:
        return self._api(
            "payment/check-status",
            {"merchantId": self.config.merchant_id, "refLabel": reference},
            ["QR EXPIRED"],
        )

    @staticmethod
    def _status_result(reference: str, body: LosslessObject) -> PaymentStatusResult:
        if get(body, "errorCode") == "QR EXPIRED":
            return PaymentStatusResult(
                status=PaymentStatus.EXPIRED,
                gateway_status="QR EXPIRED",
                gateway_reference=reference,
                raw=to_plain_object(body),
            )
        payment_status = trimmed(body, "paymentStatus")
        return PaymentStatusResult(
            status=resolve_status(_STATUSES, payment_status.upper()),
            gateway_status=payment_status,
            gateway_reference=get(body, "refLabel") or reference,
            raw=to_plain_object(body),
        )

    @staticmethod
    def _body(call: _Call, response: GatewayResponse) -> LosslessObject:
        body = response.json()
        error_code = get(body, "errorCode")
        if not response.successful() or (
            error_code != "" and error_code not in call.allowed_errors
        ):
            raise _api_error(call.path, response.status, body, error_code)
        return body

    def _token_request(self) -> HttpRequest:
        credentials = f"{self.config.client_id}:{self.config.client_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return form_request(
            f"{self.config.base_url}/token",
            {"grant_type": "client_credentials"},
            {"Authorization": f"Basic {encoded}"},
        )

    @staticmethod
    def _token(response: GatewayResponse) -> tuple[str, int]:
        """The access token and how many seconds to cache it."""
        body = response.json()
        token = get(body, "access_token")
        if not response.successful() or token == "":
            raise _api_error("token", response.status, body, get(body, "error"))
        match = _LEADING_INT.match(get(body, "expires_in"))
        expires_in = int(match.group(1)) if match else 0
        return token, max(60, (expires_in if expires_in > 0 else 3600) - 60)

    def _token_cache_key(self) -> str:
        digest = sha256_hex(f"{self.config.base_url}|{self.config.client_id}")
        return f"myanmar-payments.yoma-mmqr.token.{digest}"


def _api_error(endpoint: str, status: int, body: LosslessObject, error_code: str) -> ApiError:
    message = optional(body, "errorDescription") or optional(body, "error_description")
    return ApiError(
        f"Yoma MMQR {endpoint} failed: [{error_code}] {message or ''}".rstrip()
        if error_code
        else f"Yoma MMQR {endpoint} failed with HTTP {status}.",
        gateway_code=error_code or None,
        gateway_message=message or None,
        http_status=status,
        raw=to_plain_object(body),
    )


class YomaMmqr(_YomaMmqrBase, SyncGateway):
    """Yoma Bank MMQR with a synchronous HTTP client.

    QR payments, status checks and callbacks. Access tokens are cached in a
    :class:`~python_myanmar_payments.TokenCache`; share the gateway (or pass a
    shared cache) so they survive between calls. Use :class:`AsyncYomaMmqr` in
    async code.
    """

    def __init__(
        self,
        config: YomaMmqrConfig | Mapping[str, Any],
        *,
        token_cache: TokenCache | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(config)
        self._init_transport(http_client, self.config.timeout_seconds)
        self._cache: TokenCache = token_cache if token_cache is not None else MemoryTokenCache()
        self._token_lock = threading.Lock()

    @classmethod
    def from_env(
        cls,
        env: EnvSource | None = None,
        *,
        token_cache: TokenCache | None = None,
        http_client: httpx.Client | None = None,
    ) -> YomaMmqr:
        """A gateway configured from the ``YOMA_MMQR_*`` environment variables."""
        return cls(
            YomaMmqrConfig.from_env(env),
            token_cache=token_cache,
            http_client=http_client,
        )

    def initiate(self, data: YomaMmqrPaymentData) -> QrPayment:
        """Checks the order out with Yoma and generates its first QR.

        Call it once per order.
        """
        self._check_checkout(self._send(self._checkout_call(data)))
        return self.renew_qr(data.order_id)

    def renew_qr(self, order_id: str) -> QrPayment:
        """Generates a new QR for an order that is already checked out.

        Use it after the previous QR expired. The previous QR's reference stops
        working.
        """
        return self._qr_payment(order_id, self._send(self._renew_call(order_id)))

    def status(self, reference: str) -> PaymentStatusResult:
        """Checks a QR's payment status by its reference (``QrPayment.reference``).

        An expired QR reports ``expired``.
        """
        return self._status_result(reference, self._send(self._status_call(reference)))

    def forget_token(self) -> None:
        """Drops the cached access token, e.g. after rotating the client secret."""
        self._cache.delete(self._token_cache_key())

    def _send(self, call: _Call) -> LosslessObject:
        response = self._transport.send(call.request(self._access_token()))
        if response.status == 401:
            self.forget_token()
            response = self._transport.send(call.request(self._access_token()))
        return self._body(call, response)

    def _access_token(self) -> str:
        key = self._token_cache_key()
        cached = self._cache.get(key)
        if isinstance(cached, str) and cached != "":
            return cached
        # Concurrent calls share one token request.
        with self._token_lock:
            cached = self._cache.get(key)
            if isinstance(cached, str) and cached != "":
                return cached
            token, ttl = self._token(self._transport.send(self._token_request()))
            self._cache.set(key, token, ttl)
            return token


AnyTokenCache = TokenCache | AsyncTokenCache


class AsyncYomaMmqr(_YomaMmqrBase, AsyncGateway):
    """Yoma Bank MMQR with an async HTTP client. The same API as :class:`YomaMmqr`, awaited.

    ``token_cache`` takes a :class:`~python_myanmar_payments.TokenCache` or an
    :class:`~python_myanmar_payments.AsyncTokenCache`.
    """

    def __init__(
        self,
        config: YomaMmqrConfig | Mapping[str, Any],
        *,
        token_cache: AnyTokenCache | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config)
        self._init_transport(http_client, self.config.timeout_seconds)
        self._cache: AnyTokenCache = token_cache if token_cache is not None else MemoryTokenCache()
        self._token_lock = asyncio.Lock()

    @classmethod
    def from_env(
        cls,
        env: EnvSource | None = None,
        *,
        token_cache: AnyTokenCache | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> AsyncYomaMmqr:
        """A gateway configured from the ``YOMA_MMQR_*`` environment variables."""
        return cls(
            YomaMmqrConfig.from_env(env),
            token_cache=token_cache,
            http_client=http_client,
        )

    async def initiate(self, data: YomaMmqrPaymentData) -> QrPayment:
        """Checks the order out with Yoma and generates its first QR. Call it once per order."""
        self._check_checkout(await self._send(self._checkout_call(data)))
        return await self.renew_qr(data.order_id)

    async def renew_qr(self, order_id: str) -> QrPayment:
        """Generates a new QR for an order that is already checked out."""
        return self._qr_payment(order_id, await self._send(self._renew_call(order_id)))

    async def status(self, reference: str) -> PaymentStatusResult:
        """Checks a QR's payment status by its reference. An expired QR reports ``expired``."""
        return self._status_result(reference, await self._send(self._status_call(reference)))

    async def forget_token(self) -> None:
        """Drops the cached access token, e.g. after rotating the client secret."""
        await _resolve(self._cache.delete(self._token_cache_key()))

    async def _send(self, call: _Call) -> LosslessObject:
        response = await self._transport.send(call.request(await self._access_token()))
        if response.status == 401:
            await self.forget_token()
            response = await self._transport.send(call.request(await self._access_token()))
        return self._body(call, response)

    async def _cached_token(self, key: str) -> str | None:
        cached = await _resolve(self._cache.get(key))
        return cached if isinstance(cached, str) and cached != "" else None

    async def _access_token(self) -> str:
        key = self._token_cache_key()
        cached = await self._cached_token(key)
        if cached is not None:
            return cached
        # Concurrent calls share one token request.
        async with self._token_lock:
            cached = await self._cached_token(key)
            if cached is not None:
                return cached
            token, ttl = self._token(await self._transport.send(self._token_request()))
            await _resolve(self._cache.set(key, token, ttl))
            return token


async def _resolve(value: Any) -> Any:
    """Awaits ``value`` when a cache method returned an awaitable."""
    if inspect.isawaitable(value):
        return await cast("Any", value)
    return value
