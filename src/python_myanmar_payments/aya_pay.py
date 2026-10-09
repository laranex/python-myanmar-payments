"""The AYA Payment Gateway (APG): hosted checkout, channels, enquiry and callbacks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from ._amount import AmountInput, to_amount
from ._callback import CallbackRequest, lossless_input, lossless_query_input
from ._errors import ApiError, SignatureVerificationError
from ._http import (
    DEFAULT_TIMEOUT,
    AsyncGateway,
    GatewayResponse,
    HttpRequest,
    SyncGateway,
    json_request,
)
from ._json import LosslessObject, parse_object, to_plain_object
from ._results import FormField, FormPayment, PaymentCallback, PaymentStatusResult
from ._status import PaymentStatus, _StrEnum, resolve_status
from ._support import (
    EnvSource,
    decode_base64,
    default_env,
    env_first,
    env_sandbox,
    hmac_sha256_hex,
    optional_setting,
    require_setting,
    safe_equal,
    trim_url,
    unix_time,
)
from ._validate import AmountRule, Validator
from ._values import get, object_at, optional, scalar_string, trimmed

__all__ = [
    "AsyncAyaPay",
    "AyaPay",
    "AyaPayConfig",
    "AyaPayMethod",
    "AyaPayPaymentData",
    "AyaPayService",
]


class AyaPayConfig:
    """AYA Payment Gateway (APG) credentials and endpoints.

    A missing credential raises a :class:`~python_myanmar_payments.ConfigurationError`.
    """

    SANDBOX_URL: ClassVar[str] = "https://uat-pgw.ayainnovation.com"
    PRODUCTION_URL: ClassVar[str] = "https://pgw.ayainnovation.com"

    app_key: str
    """The public application key, sent with every request."""
    app_secret: str
    """The secret that signs requests and verifies callbacks."""
    sandbox: bool
    """Whether the UAT environment is used."""
    base_url: str
    """The base URL in use."""

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        sandbox: bool = True,
        base_url: str | None = None,
    ) -> None:
        self.app_key = require_setting("aya_pay", "app_key", app_key)
        self.app_secret = require_setting("aya_pay", "app_secret", app_secret)
        self.sandbox = sandbox
        self.base_url = trim_url(
            optional_setting(base_url) or (self.SANDBOX_URL if sandbox else self.PRODUCTION_URL)
        )

    @classmethod
    def from_env(cls, env: EnvSource | None = None) -> AyaPayConfig:
        """Reads the ``AYA_PAY_*`` environment variables.

        ``AYA_PAY_APP_KEY``, ``AYA_PAY_APP_SECRET``, ``AYA_PAY_SANDBOX`` and
        ``AYA_PAY_BASE_URL``, falling back to the ``AYA_PGW_*`` names. Defaults to
        ``os.environ``.
        """
        env = default_env() if env is None else env
        return cls(
            app_key=env_first(env, "AYA_PAY_APP_KEY", "AYA_PGW_APP_KEY"),
            app_secret=env_first(env, "AYA_PAY_APP_SECRET", "AYA_PGW_APP_SECRET"),
            sandbox=env_sandbox(env, "AYA_PAY_SANDBOX"),
            base_url=env_first(env, "AYA_PAY_BASE_URL", "AYA_PGW_BASE_URL"),
        )

    def __repr__(self) -> str:
        return f"AyaPayConfig(app_key={self.app_key!r}, sandbox={self.sandbox!r})"


class AyaPayMethod(_StrEnum):
    """How the customer pays through the chosen channel.

    :meth:`AyaPay.services` lists the methods each channel supports.
    """

    WEB = "WEB"
    """Pay on a hosted web page (cards, web checkout)."""
    QR = "QR"
    """Scan a QR with the wallet app."""
    NOTI = "NOTI"
    """Approve a push notification in the wallet app."""


_METHODS = frozenset(method.value for method in AyaPayMethod)


@dataclass(kw_only=True)
class AyaPayPaymentData:
    """An AYA Payment Gateway order.

    AYA only accepts MMK (currency code 104) and documents no decimals, so
    amounts are whole kyat.
    """

    order_id: str
    """Your unique order ID (``merchOrderId``), 6 to 40 characters."""
    amount: AmountInput
    """The amount in whole kyat, e.g. ``Amount.kyat(1000)`` or ``1000``."""
    channel: str
    """The channel key from ``services()``, e.g. ``aya_pay``, ``kbz_pay``, ``visa``."""
    method: AyaPayMethod
    """How the customer pays through that channel."""
    return_url: str | None = None
    """Where AYA sends the customer afterwards. Defaults to the URL registered with
    AYA."""
    description: str | None = None
    """Shown to the customer."""
    user_refs: Sequence[str] | None = None
    """Up to five of your own reference values, echoed back in the callback."""


@dataclass(frozen=True, kw_only=True)
class AyaPayService:
    """A payment channel enabled for your merchant account."""

    name: str
    """The display name, e.g. ``AYA Pay``."""
    key: str
    """The ``channel`` value to send when initiating, e.g. ``aya_pay`` or ``visa``."""
    image_url: str | None = None
    """The channel's logo."""
    methods: tuple[AyaPayMethod, ...] = ()
    """The methods this channel supports."""
    unknown_methods: tuple[str, ...] = ()
    """Methods the gateway listed that this package does not know yet."""

    def supports(self, method: AyaPayMethod | str) -> bool:
        """Whether the channel supports ``method``."""
        return method in self.methods


_CURRENCY_MMK = "104"

_STATUSES: Mapping[str, PaymentStatus] = {
    "00": PaymentStatus.SUCCESSFUL,
    "01": PaymentStatus.PENDING,
    "02": PaymentStatus.FAILED,
    "03": PaymentStatus.FAILED,
    "04": PaymentStatus.EXPIRED,
}

# The documented field order of the decoded callback / enquiry payload. AYA leaves
# out fields that do not apply (e.g. card fields for wallet payments) and signs only
# the ones present. The payload spells `currencyCode` as `currenyCode`.
_PAYLOAD_FIELDS = (
    "merchOrderId",
    "tranId",
    "amount",
    "currencyCode",
    "statusCode",
    "paymentCardNumber",
    "paymentMobileNumber",
    "cardTypeName",
    "cardExpiryDate",
    "nameOnCard",
    "approvalCode",
    "tranRef",
    "userRef1",
    "userRef2",
    "userRef3",
    "userRef4",
    "userRef5",
    "description",
    "dateTime",
)


@dataclass(frozen=True)
class _Call:
    endpoint: str
    request: HttpRequest


class _AyaPayBase:
    """What the sync and async AYA clients share: signing, validation, parsing."""

    config: AyaPayConfig
    """The configuration in use."""

    def __init__(self, config: AyaPayConfig) -> None:
        self.config = config

    @staticmethod
    def validate(data: AyaPayPaymentData) -> None:
        """Checks the order against AYA's documented rules.

        Raises an :class:`~python_myanmar_payments.InvalidPaymentDataError`.
        """
        user_refs: object = data.user_refs
        refs_are_list = isinstance(user_refs, (list, tuple))
        (
            Validator()
            .required("order_id", data.order_id)
            .length("order_id", data.order_id, 6, 40)
            .amount("amount", data.amount, AmountRule("AYA Payment Gateway", 0))
            .required("channel", data.channel)
            .when(
                not isinstance(data.method, str) or data.method not in _METHODS,
                "method",
                "The method field must be one of WEB, QR or NOTI.",
            )
            .string("return_url", data.return_url)
            .url("return_url", data.return_url)
            .string("description", data.description)
            .when(
                user_refs is not None
                and (
                    not refs_are_list or any(not isinstance(ref, str) for ref in user_refs)  # type: ignore[attr-defined]
                ),
                "user_refs",
                "The user_refs field must be a list of strings.",
            )
            .when(
                refs_are_list and len(user_refs) > 5,  # type: ignore[arg-type]
                "user_refs",
                "The user_refs field must not have more than 5 items.",
            )
            .validate()
        )

    def initiate(self, data: AyaPayPaymentData) -> FormPayment:
        """Signs the order. Makes no network call.

        The customer's browser must POST the returned form to AYA's hosted
        checkout; ``to_html()`` renders a page that does it.
        """
        self.validate(data)

        refs = [*(data.user_refs or []), "", "", "", "", ""][:5]
        values = [
            ("merchOrderId", data.order_id),
            ("amount", str(to_amount(data.amount))),
            ("appKey", self.config.app_key),
            ("timestamp", str(unix_time())),
            ("userRef1", refs[0]),
            ("userRef2", refs[1]),
            ("userRef3", refs[2]),
            ("userRef4", refs[3]),
            ("userRef5", refs[4]),
            ("description", data.description or ""),
            ("currencyCode", _CURRENCY_MMK),
            ("channel", data.channel),
            ("method", str(data.method)),
            ("overrideFrontendRedirectUrl", data.return_url or ""),
        ]
        values.append(("checkSum", self._checksum([value for _, value in values])))

        return FormPayment(
            order_id=data.order_id,
            action=f"{self.config.base_url}/v1/payment/request",
            fields=tuple(FormField(name, value) for name, value in values),
            enctype="multipart/form-data",
        )

    def handle_callback(self, request: CallbackRequest) -> PaymentCallback:
        """Verifies AYA's backend callback."""
        return _to_callback(self._verified_payload(lossless_input(request), "callback"))

    def verify_redirect(self, request: CallbackRequest) -> PaymentCallback:
        """Verifies the signed query string AYA adds when it sends the customer back.

        Use it to show the right page; fulfill orders from the backend callback.
        """
        return _to_callback(self._verified_payload(lossless_query_input(request), "redirect"))

    # --- Requests and results shared by both clients ------------------------------

    def _services_call(self) -> _Call:
        timestamp = unix_time()
        return self._call(
            "services",
            {
                "appKey": self.config.app_key,
                "timestamp": timestamp,
                "checkSum": self._checksum(
                    [self.config.app_key, self.config.app_secret, str(timestamp)]
                ),
            },
        )

    def _status_call(self, order_id: str) -> _Call:
        timestamp = unix_time()
        return self._call(
            "enquiry",
            {
                "merchOrderId": order_id,
                "appKey": self.config.app_key,
                "timestamp": timestamp,
                "checkSum": self._checksum([order_id, str(timestamp), self.config.app_key]),
            },
        )

    def _call(self, endpoint: str, data: Mapping[str, Any]) -> _Call:
        return _Call(endpoint, json_request(f"{self.config.base_url}/v1/payment/{endpoint}", data))

    @staticmethod
    def _body(call: _Call, response: GatewayResponse) -> LosslessObject:
        body = response.json()
        status = get(body, "status")
        if not response.successful() or status != "00":
            message = optional(body, "message")
            raise ApiError(
                f"AYA Pay {call.endpoint} failed: [{status}] {message or ''}".rstrip()
                if status
                else f"AYA Pay {call.endpoint} failed with HTTP {response.status}.",
                gateway_code=status or None,
                gateway_message=message,
                http_status=response.status,
                raw=to_plain_object(body),
            )
        return body

    def _services(self, call: _Call, response: GatewayResponse) -> list[AyaPayService]:
        body = self._body(call, response)
        entries = body.get("data")
        services: list[AyaPayService] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or get(entry, "key") == "":
                continue
            methods: list[AyaPayMethod] = []
            unknown: list[str] = []
            listed = entry.get("methods")
            for method in listed if isinstance(listed, list) else []:
                name = scalar_string(method) or ""
                if name in _METHODS:
                    methods.append(AyaPayMethod(name))
                else:
                    unknown.append(name)
            services.append(
                AyaPayService(
                    name=get(entry, "name") or get(entry, "key"),
                    key=get(entry, "key"),
                    image_url=optional(entry, "image_url"),
                    methods=tuple(methods),
                    unknown_methods=tuple(unknown),
                )
            )
        return services

    def _status_result(
        self, call: _Call, response: GatewayResponse, order_id: str
    ) -> PaymentStatusResult:
        body = self._body(call, response)
        payload = self._verified_payload(object_at(body, "data") or {}, "enquiry response")
        status_code = trimmed(payload, "statusCode")
        return PaymentStatusResult(
            order_id=optional(payload, "merchOrderId") or order_id,
            status=resolve_status(_STATUSES, status_code),
            gateway_status=status_code,
            gateway_reference=optional(payload, "tranId"),
            amount=optional(payload, "amount"),
            raw=to_plain_object(payload),
        )

    def _verified_payload(self, data: LosslessObject, context: str) -> LosslessObject:
        """Decodes a ``payload`` + ``checkSum`` pair; returns the payload if it matches."""

        def fail() -> SignatureVerificationError:
            return SignatureVerificationError(
                f"AYA Pay {context} checksum verification failed.", to_plain_object(data)
            )

        # Base64 never contains spaces: a space is a `+` that an unencoded query
        # string turned into one.
        text = decode_base64(get(data, "payload").replace(" ", "+"))
        payload = None if text is None else parse_object(text)
        if payload is None:
            raise fail()

        parts: list[str] = []
        for name in _PAYLOAD_FIELDS:
            key = "currenyCode" if name == "currencyCode" and "currenyCode" in payload else name
            if key in payload:
                parts.append(scalar_string(payload[key]) or "")

        if not safe_equal(self._checksum(parts), get(data, "checkSum").lower()):
            raise fail()
        return payload

    def _checksum(self, parts: Sequence[str]) -> str:
        return hmac_sha256_hex(self.config.app_secret, ":".join(parts))


def _to_callback(payload: LosslessObject) -> PaymentCallback:
    status_code = trimmed(payload, "statusCode")
    return PaymentCallback(
        order_id=get(payload, "merchOrderId"),
        status=resolve_status(_STATUSES, status_code),
        gateway_status=status_code,
        gateway_reference=optional(payload, "tranId"),
        amount=optional(payload, "amount"),
        raw=to_plain_object(payload),
    )


class AyaPay(_AyaPayBase, SyncGateway):
    """The AYA Payment Gateway with a synchronous HTTP client.

    One hosted checkout for AYA Pay, other wallets and cards, with channel
    listing, status enquiry and verified callbacks. Use :class:`AsyncAyaPay` in
    async code.
    """

    def __init__(
        self,
        config: AyaPayConfig,
        *,
        http_client: httpx.Client | None = None,
        timeout: float | None = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(config)
        self._init_transport(http_client, timeout)

    @classmethod
    def from_env(
        cls,
        env: EnvSource | None = None,
        *,
        http_client: httpx.Client | None = None,
        timeout: float | None = DEFAULT_TIMEOUT,
    ) -> AyaPay:
        """A gateway configured from the ``AYA_PAY_*`` (or ``AYA_PGW_*``) variables."""
        return cls(AyaPayConfig.from_env(env), http_client=http_client, timeout=timeout)

    def services(self) -> list[AyaPayService]:
        """Lists the payment channels enabled for your merchant account."""
        call = self._services_call()
        return self._services(call, self._transport.send(call.request))

    def status(self, order_id: str) -> PaymentStatusResult:
        """Asks AYA for the current state of an order (enquiry)."""
        call = self._status_call(order_id)
        return self._status_result(call, self._transport.send(call.request), order_id)


class AsyncAyaPay(_AyaPayBase, AsyncGateway):
    """The AYA Payment Gateway with an async HTTP client.

    The same API as :class:`AyaPay`; ``services()`` and ``status()`` are awaited.
    """

    def __init__(
        self,
        config: AyaPayConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float | None = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(config)
        self._init_transport(http_client, timeout)

    @classmethod
    def from_env(
        cls,
        env: EnvSource | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float | None = DEFAULT_TIMEOUT,
    ) -> AsyncAyaPay:
        """A gateway configured from the ``AYA_PAY_*`` (or ``AYA_PGW_*``) variables."""
        return cls(AyaPayConfig.from_env(env), http_client=http_client, timeout=timeout)

    async def services(self) -> list[AyaPayService]:
        """Lists the payment channels enabled for your merchant account."""
        call = self._services_call()
        return self._services(call, await self._transport.send(call.request))

    async def status(self, order_id: str) -> PaymentStatusResult:
        """Asks AYA for the current state of an order (enquiry)."""
        call = self._status_call(order_id)
        return self._status_result(call, await self._transport.send(call.request), order_id)
