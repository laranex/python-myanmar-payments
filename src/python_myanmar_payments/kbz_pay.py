"""KBZ Pay: PWA (redirect), QR and in-app payments, order status and notifications."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, ClassVar

import httpx

from ._amount import AmountInput, to_amount
from ._callback import CallbackRequest, lossless_body
from ._errors import ApiError, SignatureVerificationError
from ._http import (
    AsyncGateway,
    GatewayResponse,
    HttpRequest,
    SyncGateway,
    json_request,
)
from ._json import LosslessObject, to_plain_object
from ._results import (
    Acknowledgement,
    AppPayment,
    PaymentCallback,
    PaymentStatusResult,
    QrPayment,
    RedirectPayment,
)
from ._status import PaymentStatus, resolve_status
from ._support import (
    EnvSource,
    config_of,
    default_env,
    env_first,
    optional_setting,
    query_escape,
    random_hex,
    require_seconds,
    require_setting,
    safe_equal,
    sha256_hex,
    trim_url,
    unix_time,
    utc_now,
)
from ._validate import AmountRule, Validator
from ._values import get, is_nested, object_at, optional, scalar_string, trimmed

__all__ = [
    "AsyncKbzPay",
    "KbzPay",
    "KbzPayConfig",
    "KbzPayPaymentData",
    "KbzPaySigner",
]


class KbzPayConfig:
    """KBZ Pay credentials and endpoints.

    Every setting except the URL overrides is required; a missing one raises a
    :class:`~python_myanmar_payments.ConfigurationError`. The URLs default to
    production.
    """

    PRODUCTION_API_URL: ClassVar[str] = "https://api.kbzpay.com/payment/gateway"
    PRODUCTION_PWA_URL: ClassVar[str] = "https://wap.kbzpay.com/pgw/pwa/#/"

    app_id: str
    """The ``appid`` KBZ issued for your merchant app."""
    app_key: str
    """The secret key used to sign requests."""
    merchant_code: str
    """The ``merch_code`` KBZ issued."""
    timeout_seconds: int
    """Seconds before the default HTTP client gives up on a request."""
    api_url: str
    """The API base URL in use."""
    pwa_url: str
    """The PWA checkout URL in use, always ending in ``/``."""

    def __init__(
        self,
        *,
        app_id: str = "",
        app_key: str = "",
        merchant_code: str = "",
        timeout_seconds: int | str | None = None,
        api_url: str | None = None,
        pwa_url: str | None = None,
    ) -> None:
        self.app_id = require_setting("kbz_pay", "app_id", app_id)
        self.app_key = require_setting("kbz_pay", "app_key", app_key)
        self.merchant_code = require_setting("kbz_pay", "merchant_code", merchant_code)
        self.timeout_seconds = require_seconds("kbz_pay", "timeout_in_seconds", timeout_seconds)
        self.api_url = trim_url(optional_setting(api_url) or self.PRODUCTION_API_URL)
        self.pwa_url = trim_url(optional_setting(pwa_url) or self.PRODUCTION_PWA_URL) + "/"

    @classmethod
    def from_env(cls, env: EnvSource | None = None) -> KbzPayConfig:
        """Reads the ``KBZ_PAY_*`` environment variables.

        ``KBZ_PAY_APP_ID``, ``KBZ_PAY_APP_KEY``, ``KBZ_PAY_MERCHANT_CODE``,
        ``MYANMAR_PAYMENTS_HTTP_TIMEOUT``, ``KBZ_PAY_BASE_URL`` and
        ``KBZ_PAY_PWA_BASE_REDIRECT_URL``. Defaults to ``os.environ``.
        """
        env = default_env() if env is None else env
        return cls(
            app_id=env_first(env, "KBZ_PAY_APP_ID"),
            app_key=env_first(env, "KBZ_PAY_APP_KEY"),
            merchant_code=env_first(env, "KBZ_PAY_MERCHANT_CODE"),
            timeout_seconds=env_first(env, "MYANMAR_PAYMENTS_HTTP_TIMEOUT"),
            api_url=env_first(env, "KBZ_PAY_BASE_URL"),
            pwa_url=env_first(env, "KBZ_PAY_PWA_BASE_REDIRECT_URL"),
        )

    def __repr__(self) -> str:
        return f"KbzPayConfig(app_id={self.app_id!r})"


@dataclass(kw_only=True)
class KbzPayPaymentData:
    """A KBZ Pay order, used for the PWA, QR and in-app flows alike.

    KBZ Pay only accepts MMK.
    """

    order_id: str
    """Your unique order ID (``merch_order_id``): letters, digits and ``_`` only, at
    most 40."""
    amount: AmountInput
    """Kyat, greater than 0 with at most 2 decimal places, e.g. ``Amount.kyat(1000)``
    or ``Amount.parse("1000.50")``."""
    callback_url: str
    """The public URL KBZ posts the result to (``notify_url``): at most 512
    characters, no query string."""
    title: str | None = None
    """The product name shown to the customer."""
    timeout_minutes: int | None = None
    """How long the order stays payable, 1 to 120 minutes. KBZ defaults to 120."""
    callback_info: str | None = None
    """Free text KBZ sends back unchanged in the callback, at most 512 characters
    once URL-encoded."""


class KbzPaySigner:
    """KBZ Pay's signature.

    Every non-empty scalar field except ``sign`` and ``sign_type``, sorted by
    key, joined as raw ``key=value`` pairs (not URL-encoded), with
    ``&key=<app key>`` appended, hashed with SHA-256 and uppercased.
    """

    def __init__(self, app_key: str) -> None:
        self._app_key = app_key

    def sign_string(self, fields: Mapping[str, object]) -> str:
        """The sorted ``key=value`` string, without the app key."""
        pairs = []
        for key in sorted(fields):
            if key in ("sign", "sign_type"):
                continue
            value = scalar_string(fields[key])
            if value is not None and value != "":
                pairs.append(f"{key}={value}")
        return "&".join(pairs)

    def sign(self, fields: Mapping[str, object]) -> str:
        """The uppercase SHA-256 signature of ``fields``."""
        return sha256_hex(f"{self.sign_string(fields)}&key={self._app_key}").upper()

    def verify(self, fields: Mapping[str, object]) -> bool:
        """Whether ``fields["sign"]`` matches, compared in constant time.

        Nested values are never signed, so fields carrying one are rejected
        rather than partly trusted.
        """
        if any(is_nested(value) for value in fields.values()):
            return False
        sign = fields.get("sign")
        return isinstance(sign, str) and safe_equal(self.sign(fields), sign.upper())


_ORDER_ID = re.compile(r"[A-Za-z0-9_]+")

_STATUSES: Mapping[str, PaymentStatus] = {
    "PAY_SUCCESS": PaymentStatus.SUCCESSFUL,
    "WAIT_PAY": PaymentStatus.PENDING,
    "PAYING": PaymentStatus.PENDING,
    "PAY_FAILED": PaymentStatus.FAILED,
    "ORDER_CLOSED": PaymentStatus.CANCELED,
    "ORDER_EXPIRED": PaymentStatus.EXPIRED,
}


@dataclass(frozen=True)
class _Call:
    endpoint: str
    request: HttpRequest
    nonce: str
    timestamp: str


@dataclass(frozen=True)
class _Order:
    prepay_id: str
    nonce: str
    timestamp: str
    response: LosslessObject


class _KbzPayBase:
    """What the sync and async KBZ Pay clients share: signing, validation, parsing."""

    config: KbzPayConfig
    """The configuration in use."""
    signer: KbzPaySigner
    """KBZ Pay's request signer, for custom calls."""

    def __init__(self, config: KbzPayConfig | Mapping[str, Any]) -> None:
        self.config = config_of(KbzPayConfig, config)
        self.signer = KbzPaySigner(self.config.app_key)

    @staticmethod
    def validate(data: KbzPayPaymentData) -> None:
        """Checks the order against KBZ's documented limits.

        Raises an :class:`~python_myanmar_payments.InvalidPaymentDataError`.
        """
        callback_url = data.callback_url if isinstance(data.callback_url, str) else ""
        callback_info = data.callback_info
        (
            Validator()
            .required("order_id", data.order_id)
            .max("order_id", data.order_id, 40)
            .pattern("order_id", data.order_id, _ORDER_ID, "letters, numbers and underscores")
            .amount("amount", data.amount, AmountRule("KBZ Pay", 2))
            .required("callback_url", data.callback_url)
            .url("callback_url", callback_url)
            .max("callback_url", callback_url, 512)
            .when(
                "?" in callback_url,
                "callback_url",
                "The callback_url field must not contain a query string.",
            )
            .string("title", data.title)
            .between("timeout_minutes", data.timeout_minutes, 1, 120)
            .string("callback_info", callback_info)
            .max(
                "callback_info",
                query_escape(callback_info) if isinstance(callback_info, str) else None,
                512,
            )
            .validate()
        )

    def handle_callback(self, request: CallbackRequest) -> PaymentCallback:
        """Verifies KBZ Pay's payment notification.

        Reply with the callback's ``acknowledgement`` (plain ``success``) or KBZ
        retries.
        """
        payload = lossless_body(request)
        request_fields = object_at(payload, "Request")
        fields = payload if request_fields is None else request_fields

        if not self.signer.verify(fields):
            raise SignatureVerificationError(
                "KBZ Pay callback signature verification failed.", to_plain_object(payload)
            )

        trade_status = trimmed(fields, "trade_status")
        return PaymentCallback(
            order_id=get(fields, "merch_order_id"),
            status=resolve_status(_STATUSES, trade_status),
            gateway_status=trade_status,
            gateway_reference=optional(fields, "mm_order_id"),
            amount=optional(fields, "total_amount"),
            raw=to_plain_object(fields),
            acknowledgement=Acknowledgement(body="success"),
        )

    # --- Requests and results shared by both clients ------------------------------

    def _precreate_call(self, data: KbzPayPaymentData, trade_type: str) -> _Call:
        self.validate(data)
        biz = {
            "appid": self.config.app_id,
            "merch_code": self.config.merchant_code,
            "merch_order_id": data.order_id,
            "trade_type": trade_type,
            "total_amount": str(to_amount(data.amount)),
            "trans_currency": "MMK",
        }
        if data.title:
            biz["title"] = data.title
        if data.timeout_minutes is not None:
            biz["timeout_express"] = f"{data.timeout_minutes}m"
        if data.callback_info:
            biz["callback_info"] = query_escape(data.callback_info)
        return self._call(
            "precreate",
            "kbz.payment.precreate",
            "1.0",
            biz,
            {"notify_url": data.callback_url},
        )

    def _status_call(self, order_id: str) -> _Call:
        return self._call(
            "queryorder",
            "kbz.payment.queryorder",
            "3.0",
            {
                "appid": self.config.app_id,
                "merch_code": self.config.merchant_code,
                "merch_order_id": order_id,
            },
            {},
        )

    def _call(
        self,
        endpoint: str,
        method: str,
        version: str,
        biz: dict[str, str],
        extra: dict[str, str],
    ) -> _Call:
        nonce = random_hex()
        timestamp = str(unix_time())
        common = {
            "timestamp": timestamp,
            "method": method,
            "nonce_str": nonce,
            "version": version,
            **extra,
        }
        request = {
            **common,
            "sign_type": "SHA256",
            "sign": self.signer.sign({**common, **biz}),
            "biz_content": biz,
        }
        return _Call(
            endpoint,
            json_request(f"{self.config.api_url}/{endpoint}", {"Request": request}),
            nonce,
            timestamp,
        )

    @staticmethod
    def _result(call: _Call, response: GatewayResponse) -> LosslessObject:
        """The ``Response`` object, raising when KBZ reports an error."""
        body = response.json()
        result = object_at(body, "Response") or {}
        if (
            not response.successful()
            or get(result, "result") != "SUCCESS"
            or get(result, "code") != "0"
        ):
            code = optional(result, "code")
            message = optional(result, "msg")
            raise ApiError(
                f"KBZ Pay {call.endpoint} failed: [{code}] {message or ''}".rstrip()
                if code
                else f"KBZ Pay {call.endpoint} failed with HTTP {response.status}.",
                gateway_code=code,
                gateway_message=message,
                http_status=response.status,
                raw=to_plain_object(body),
            )
        return result

    def _order(self, call: _Call, response: GatewayResponse) -> _Order:
        result = self._result(call, response)
        prepay_id = get(result, "prepay_id")
        if prepay_id == "":
            raise ApiError("KBZ Pay did not return a prepay_id.", raw=to_plain_object(result))
        return _Order(prepay_id, call.nonce, call.timestamp, result)

    def _order_info_fields(self, order: _Order) -> dict[str, str]:
        return {
            "appid": self.config.app_id,
            "merch_code": self.config.merchant_code,
            "nonce_str": order.nonce,
            "prepay_id": order.prepay_id,
            "timestamp": order.timestamp,
        }

    def _pwa_payment(self, data: KbzPayPaymentData, order: _Order) -> RedirectPayment:
        fields = self._order_info_fields(order)
        query = f"{self.signer.sign_string(fields)}&sign={self.signer.sign(fields)}"
        return RedirectPayment(
            order_id=data.order_id,
            url=f"{self.config.pwa_url}?{query}",
            gateway_reference=order.prepay_id,
            raw=to_plain_object(order.response),
        )

    @staticmethod
    def _qr_payment(data: KbzPayPaymentData, order: _Order) -> QrPayment:
        qr_code = get(order.response, "qrCode")
        if qr_code == "":
            raise ApiError("KBZ Pay did not return a QR code.", raw=to_plain_object(order.response))
        return QrPayment(
            order_id=data.order_id,
            qr_string=qr_code,
            expires_at=None
            if data.timeout_minutes is None
            else utc_now() + timedelta(minutes=data.timeout_minutes),
            reference=order.prepay_id,
            raw=to_plain_object(order.response),
        )

    def _app_payment(self, data: KbzPayPaymentData, order: _Order) -> AppPayment:
        fields = self._order_info_fields(order)
        return AppPayment(
            order_id=data.order_id,
            order_info=self.signer.sign_string(fields),
            sign=self.signer.sign(fields),
            sign_type="SHA256",
            raw=to_plain_object(order.response),
        )

    def _status_result(
        self, call: _Call, response: GatewayResponse, order_id: str
    ) -> PaymentStatusResult:
        result = self._result(call, response)
        trade_status = trimmed(result, "trade_status")
        return PaymentStatusResult(
            order_id=optional(result, "merch_order_id") or order_id,
            status=resolve_status(_STATUSES, trade_status),
            gateway_status=trade_status,
            gateway_reference=optional(result, "mm_order_id"),
            amount=optional(result, "total_amount"),
            raw=to_plain_object(result),
        )


class KbzPay(_KbzPayBase, SyncGateway):
    """KBZ Pay with a synchronous HTTP client.

    PWA (redirect), QR and in-app payments, order status queries and payment
    notifications. Use :class:`AsyncKbzPay` in async code.
    """

    def __init__(
        self,
        config: KbzPayConfig | Mapping[str, Any],
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(config)
        self._init_transport(http_client, self.config.timeout_seconds)

    @classmethod
    def from_env(
        cls,
        env: EnvSource | None = None,
        *,
        http_client: httpx.Client | None = None,
    ) -> KbzPay:
        """A gateway configured from the ``KBZ_PAY_*`` environment variables."""
        return cls(KbzPayConfig.from_env(env), http_client=http_client)

    def pwa(self, data: KbzPayPaymentData) -> RedirectPayment:
        """Creates an order and returns the KBZ Pay PWA checkout URL.

        The redirect only works on a phone with the KBZ Pay app installed, and its
        Referer must match the URL registered with KBZ.
        """
        return self._pwa_payment(data, self._precreate(data, "PWAAPP"))

    def qr(self, data: KbzPayPaymentData) -> QrPayment:
        """Creates an order and returns a QR payload to scan with the KBZ Pay app."""
        return self._qr_payment(data, self._precreate(data, "PAY_BY_QRCODE"))

    def app(self, data: KbzPayPaymentData) -> AppPayment:
        """Creates an order and returns the signed values for ``KBZPay.startPay()``.

        The SDK's own result only means the payment screen closed; rely on the
        callback or :meth:`status`.
        """
        return self._app_payment(data, self._precreate(data, "APP"))

    def status(self, order_id: str) -> PaymentStatusResult:
        """Asks KBZ Pay for the current state of an order (``queryorder``)."""
        call = self._status_call(order_id)
        return self._status_result(call, self._transport.send(call.request), order_id)

    def _precreate(self, data: KbzPayPaymentData, trade_type: str) -> _Order:
        call = self._precreate_call(data, trade_type)
        return self._order(call, self._transport.send(call.request))


class AsyncKbzPay(_KbzPayBase, AsyncGateway):
    """KBZ Pay with an async HTTP client. The same API as :class:`KbzPay`, awaited."""

    def __init__(
        self,
        config: KbzPayConfig | Mapping[str, Any],
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config)
        self._init_transport(http_client, self.config.timeout_seconds)

    @classmethod
    def from_env(
        cls,
        env: EnvSource | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> AsyncKbzPay:
        """A gateway configured from the ``KBZ_PAY_*`` environment variables."""
        return cls(KbzPayConfig.from_env(env), http_client=http_client)

    async def pwa(self, data: KbzPayPaymentData) -> RedirectPayment:
        """Creates an order and returns the KBZ Pay PWA checkout URL."""
        return self._pwa_payment(data, await self._precreate(data, "PWAAPP"))

    async def qr(self, data: KbzPayPaymentData) -> QrPayment:
        """Creates an order and returns a QR payload to scan with the KBZ Pay app."""
        return self._qr_payment(data, await self._precreate(data, "PAY_BY_QRCODE"))

    async def app(self, data: KbzPayPaymentData) -> AppPayment:
        """Creates an order and returns the signed values for ``KBZPay.startPay()``."""
        return self._app_payment(data, await self._precreate(data, "APP"))

    async def status(self, order_id: str) -> PaymentStatusResult:
        """Asks KBZ Pay for the current state of an order (``queryorder``)."""
        call = self._status_call(order_id)
        return self._status_result(call, await self._transport.send(call.request), order_id)

    async def _precreate(self, data: KbzPayPaymentData, trade_type: str) -> _Order:
        call = self._precreate_call(data, trade_type)
        return self._order(call, await self._transport.send(call.request))
