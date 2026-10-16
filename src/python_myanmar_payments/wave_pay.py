"""Wave Pay (Pay with Wave API v1.3): redirect payments and callbacks."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from ._amount import Amount, AmountInput, to_amount
from ._callback import CallbackRequest, lossless_body
from ._errors import (
    ApiError,
    SignatureVerificationError,
)
from ._http import (
    AsyncGateway,
    GatewayResponse,
    HttpRequest,
    SyncGateway,
    json_request,
)
from ._json import LosslessObject, dumps, to_plain_object
from ._results import PaymentCallback, RedirectPayment
from ._status import PaymentStatus, resolve_status
from ._support import (
    DEFAULT_TIMEOUT_SECONDS,
    EnvSource,
    config_of,
    default_env,
    env_first,
    hmac_sha256_hex,
    query_escape,
    random_hex,
    read_timeout,
    require_seconds,
    require_setting,
    safe_equal,
    trim_url,
)
from ._validate import AmountRule, Validator, is_int
from ._values import get, is_nested, optional, scalar_string, trimmed

__all__ = [
    "AsyncWavePay",
    "WavePay",
    "WavePayConfig",
    "WavePayItem",
    "WavePayPaymentData",
]


class WavePayConfig:
    """Wave Pay (Pay with Wave) credentials and endpoints.

    Every setting except the HTTP timeout is required; a missing or blank one
    raises a :class:`~python_myanmar_payments.ConfigurationError`. The timeout
    defaults to :data:`~python_myanmar_payments.DEFAULT_TIMEOUT_SECONDS`.
    :attr:`PRODUCTION_URL` names production's API and payment page host.
    """

    PRODUCTION_URL: ClassVar[str] = "https://payments.wavemoney.io"

    merchant_id: str
    """The merchant ID Wave issued."""
    secret_key: str
    """The hash secret key Wave issued."""
    merchant_name: str
    """Your business name, shown on Wave's payment page."""
    time_to_live_seconds: int
    """Seconds the customer has to pay, greater than 0. A payment's own
    ``time_to_live_seconds`` overrides it."""
    timeout_seconds: int
    """Seconds before the default HTTP client gives up on a request (default 30)."""
    base_url: str
    """The API base URL, e.g. :attr:`PRODUCTION_URL` or
    ``https://preprodpayments.wavemoney.io:8107``. Payments are requested at its
    ``/payment``."""
    base_redirect_url: str
    """The host the customer is sent to, e.g. :attr:`PRODUCTION_URL` or
    ``https://preprodpayments.wavemoney.io:8107``. The customer approves the payment at
    its ``/authenticate``."""

    def __init__(
        self,
        *,
        merchant_id: str | int = "",
        secret_key: str | int = "",
        merchant_name: str | int = "",
        time_to_live_seconds: int | str | None = None,
        timeout_seconds: int | str | None = DEFAULT_TIMEOUT_SECONDS,
        base_url: str = "",
        base_redirect_url: str = "",
    ) -> None:
        self.merchant_id = require_setting("wave_pay", "merchant_id", merchant_id)
        self.secret_key = require_setting("wave_pay", "secret_key", secret_key)
        self.merchant_name = require_setting("wave_pay", "merchant_name", merchant_name)
        self.base_url = trim_url(require_setting("wave_pay", "base_url", base_url))
        self.base_redirect_url = trim_url(
            require_setting("wave_pay", "base_redirect_url", base_redirect_url)
        )
        self.time_to_live_seconds = require_seconds(
            "wave_pay", "time_to_live_in_seconds", time_to_live_seconds
        )
        self.timeout_seconds = read_timeout("wave_pay", timeout_seconds)

    @classmethod
    def from_env(cls, env: EnvSource | None = None) -> WavePayConfig:
        """Reads the ``WAVE_PAY_*`` environment variables.

        ``WAVE_PAY_MERCHANT_ID``, ``WAVE_PAY_SECRET_KEY``,
        ``WAVE_PAY_MERCHANT_NAME``, ``WAVE_PAY_BASE_URL``,
        ``WAVE_PAY_BASE_REDIRECT_URL``, ``WAVE_PAY_TIME_TO_LIVE_IN_SECONDS`` and
        ``MYANMAR_PAYMENTS_HTTP_TIMEOUT``. Defaults to ``os.environ``.
        """
        env = default_env() if env is None else env
        return cls(
            merchant_id=env_first(env, "WAVE_PAY_MERCHANT_ID"),
            secret_key=env_first(env, "WAVE_PAY_SECRET_KEY"),
            merchant_name=env_first(env, "WAVE_PAY_MERCHANT_NAME"),
            time_to_live_seconds=env_first(env, "WAVE_PAY_TIME_TO_LIVE_IN_SECONDS"),
            timeout_seconds=env_first(env, "MYANMAR_PAYMENTS_HTTP_TIMEOUT"),
            base_url=env_first(env, "WAVE_PAY_BASE_URL"),
            base_redirect_url=env_first(env, "WAVE_PAY_BASE_REDIRECT_URL"),
        )

    def __repr__(self) -> str:
        return f"WavePayConfig(merchant_id={self.merchant_id!r})"


@dataclass(frozen=True)
class WavePayItem:
    """A line item shown on Wave's payment page."""

    name: str
    """The item name."""
    amount: AmountInput
    """The item amount in whole kyat, e.g. ``Amount.kyat(1000)`` or ``1000``."""


@dataclass(kw_only=True)
class WavePayPaymentData:
    """A Wave Pay payment request. Wave only accepts whole kyat (MMK).

    Length limits count UTF-8 bytes.
    """

    order_id: str
    """Your order ID, at most 100 characters: letters, numbers, ``_`` and ``-``.
    One order can have several payment attempts."""
    callback_url: str
    """The URL Wave posts the result to (``backend_result_url``), at most 255
    characters."""
    return_url: str
    """Where Wave sends the customer back (``frontend_result_url``), at most 255
    characters. Not proof of payment."""
    description: str
    """Shown to the customer (``payment_description``), at most 255 characters."""
    items: Sequence[WavePayItem] = field(default_factory=list)
    """The line items shown on Wave's page; at least one."""
    amount: AmountInput | None = None
    """The total in whole kyat. Leave it unset to charge the sum of the items."""
    merchant_reference_id: str | None = None
    """The unique ID of this attempt, at most 100 characters: letters, numbers,
    ``_`` and ``-``. Wave rejects a reused one.

    ``initiate()`` fills it with a random ID when empty, so store it afterwards:
    Wave's callback may omit ``orderId`` but always carries this."""
    merchant_name: str | None = None
    """Your business name shown on Wave's page for this payment. Defaults to the
    configured ``merchant_name``."""
    time_to_live_seconds: int | None = None
    """Seconds the customer has to pay this payment, greater than 0. Overrides the
    configured ``time_to_live_seconds`` for this payment."""
    additional_field_3: str | None = None
    """Extra information sent as ``additional_field_3``, at most 50 characters;
    Wave echoes it as the callback's ``additionalField3``. Not sent when ``None``
    or empty."""
    additional_field_4: str | None = None
    """Extra information sent as ``additional_field_4``, at most 50 characters;
    echoed as ``additionalField4``. Not sent when ``None`` or empty."""
    additional_field_5: str | None = None
    """Extra information sent as ``additional_field_5``, at most 50 characters;
    echoed as ``additionalField5``. Not sent when ``None`` or empty."""


_STATUSES: Mapping[str, PaymentStatus] = {
    "PAYMENT_CONFIRMED": PaymentStatus.SUCCESSFUL,
    "BILL_COLLECTION_FAILED": PaymentStatus.FAILED,
    "INSUFFICIENT_BALANCE": PaymentStatus.FAILED,
    "ACCOUNT_LOCKED": PaymentStatus.FAILED,
    "PAYMENT_REQUEST_CANCELLED": PaymentStatus.CANCELED,
    "TRANSACTION_TIMED_OUT": PaymentStatus.EXPIRED,
    "SCHEDULER_TRANSACTION_TIMED_OUT": PaymentStatus.EXPIRED,
}

_CALLBACK_FIELDS = (
    "status",
    "timeToLiveSeconds",
    "merchantId",
    "orderId",
    "amount",
    "backendResultUrl",
    "merchantReferenceId",
    "initiatorMsisdn",
    "transactionId",
    "paymentRequestId",
    "requestTime",
)

_RULE = AmountRule("Wave Pay", 0)
_CURRENCY = "MMK"
_ADDITIONAL_FIELDS = ("additional_field_3", "additional_field_4", "additional_field_5")

# Wave's hash concatenates the values with no separator, so a verified callback
# must also hold values in their expected formats.
_ID = re.compile(r"[A-Za-z0-9_-]+")
_ID_DESCRIPTION = "letters, numbers, underscores and dashes"
_AMOUNT = re.compile(r"[0-9]+(?:\.[0-9]+)?")


class _WavePayBase:
    """What the sync and async Wave Pay clients share."""

    config: WavePayConfig
    """The configuration in use."""

    def __init__(self, config: WavePayConfig | Mapping[str, Any]) -> None:
        self.config = config_of(WavePayConfig, config)

    @staticmethod
    def resolved_amount(data: WavePayPaymentData) -> Amount | None:
        """The total that will be charged: ``amount``, or the exact sum of the items.

        ``None`` when an item amount is missing, malformed or has decimals.
        """
        if data.amount is not None:
            return to_amount(data.amount)
        items = data.items
        if not isinstance(items, (list, tuple)) or len(items) == 0:
            return None
        total = 0
        for item in items:
            amount = to_amount(getattr(item, "amount", None))
            if amount is None or amount.decimal_places() > 0:
                return None
            total += int(str(amount))
        return Amount.kyat(total)

    @classmethod
    def validate(cls, data: WavePayPaymentData) -> None:
        """Checks the request against Wave's documented rules.

        Raises an :class:`~python_myanmar_payments.InvalidPaymentDataError`.
        """
        items = list(data.items) if isinstance(data.items, (list, tuple)) else []
        validator = (
            Validator()
            .required("order_id", data.order_id)
            .max("order_id", data.order_id, 100)
            .pattern("order_id", data.order_id, _ID, _ID_DESCRIPTION)
            .required("callback_url", data.callback_url)
            .url("callback_url", data.callback_url)
            .max("callback_url", data.callback_url, 255)
            .required("return_url", data.return_url)
            .url("return_url", data.return_url)
            .max("return_url", data.return_url, 255)
            .required("description", data.description)
            .max("description", data.description, 255)
            .when(len(items) == 0, "items", "The items field must have at least one item.")
            .string("merchant_reference_id", data.merchant_reference_id)
            .max("merchant_reference_id", data.merchant_reference_id, 100)
            .pattern("merchant_reference_id", data.merchant_reference_id, _ID, _ID_DESCRIPTION)
        )
        if data.merchant_name is not None:
            validator.required("merchant_name", data.merchant_name)
        ttl = data.time_to_live_seconds
        validator.when(
            ttl is not None and not is_int(ttl),
            "time_to_live_seconds",
            "The time_to_live_seconds field must be an integer.",
        ).positive("time_to_live_seconds", ttl)
        for name in _ADDITIONAL_FIELDS:
            validator.string(name, getattr(data, name)).max(name, getattr(data, name), 50)
        for index, item in enumerate(items):
            validator.required(f"items.{index}.name", getattr(item, "name", None)).amount(
                f"items.{index}.amount", getattr(item, "amount", None), _RULE
            )
        resolved = cls.resolved_amount(data)
        if not (len(items) > 0 and data.amount is None and resolved is None):
            validator.amount("amount", data.amount if data.amount is not None else resolved, _RULE)
        validator.validate()

    def handle_callback(self, request: CallbackRequest) -> PaymentCallback:
        """Verifies Wave's callback. Only ``PAYMENT_CONFIRMED`` means the customer paid.

        ``order_id`` falls back to ``merchantReferenceId`` when ``orderId`` is
        missing, null or empty, as a safeguard: earlier Wave documents marked
        ``orderId`` optional. A nested hashed value or a wrong hash fails with
        "Wave Pay callback hash verification failed."; a correctly hashed callback
        whose amount is not a non-negative decimal, that has neither id, or whose
        ``orderId`` or ``merchantReferenceId`` holds anything but letters, numbers,
        ``_`` and ``-`` fails with "Wave Pay callback is malformed.".
        """
        payload = lossless_body(request)
        nested = any(is_nested(payload.get(name)) for name in _CALLBACK_FIELDS)
        parts = []
        for name in _CALLBACK_FIELDS:
            text = scalar_string(payload.get(name))
            parts.append("null" if text is None else text)
        expected = self._hash(parts)
        hash_value = payload.get("hashValue")

        if (
            nested
            or not isinstance(hash_value, str)
            or not safe_equal(expected, hash_value.lower())
        ):
            raise SignatureVerificationError(
                "Wave Pay callback hash verification failed.", to_plain_object(payload)
            )

        order_id = get(payload, "orderId")
        reference_id = get(payload, "merchantReferenceId")
        if (
            _AMOUNT.fullmatch(get(payload, "amount")) is None
            or (order_id or reference_id) == ""
            or any(_ID.fullmatch(value) is None for value in (order_id, reference_id) if value)
        ):
            raise SignatureVerificationError(
                "Wave Pay callback is malformed.", to_plain_object(payload)
            )

        gateway_status = trimmed(payload, "status")
        return PaymentCallback(
            order_id=order_id or reference_id,
            status=resolve_status(_STATUSES, gateway_status),
            gateway_status=gateway_status,
            gateway_reference=optional(payload, "transactionId"),
            amount=optional(payload, "amount"),
            raw=to_plain_object(payload),
        )

    def _initiate_request(self, data: WavePayPaymentData) -> HttpRequest:
        self.validate(data)
        ttl = (
            self.config.time_to_live_seconds
            if data.time_to_live_seconds is None
            else data.time_to_live_seconds
        )
        if not data.merchant_reference_id:
            data.merchant_reference_id = random_hex()

        amount = str(self.resolved_amount(data))
        merchant_name = (
            self.config.merchant_name if data.merchant_name is None else data.merchant_name
        )
        # Wave documents items as [{"name": "...", "amount": 1000}] with a numeric
        # amount; the amount text is written as a JSON number without a float.
        items = ",".join(
            f'{{"name":{dumps(item.name)},"amount":{to_amount(item.amount)}}}'
            for item in data.items
        )
        body: dict[str, Any] = {
            "merchant_id": self.config.merchant_id,
            "order_id": data.order_id,
            "merchant_reference_id": data.merchant_reference_id,
            "frontend_result_url": data.return_url,
            "backend_result_url": data.callback_url,
            "amount": int(amount),
            "time_to_live_in_seconds": ttl,
            "payment_description": data.description,
            "currency": _CURRENCY,
            "hash": self._hash(
                [
                    str(ttl),
                    self.config.merchant_id,
                    data.order_id,
                    amount,
                    data.callback_url,
                    data.merchant_reference_id,
                ]
            ),
            "merchant_name": merchant_name,
            "items": f"[{items}]",
        }
        for name in _ADDITIONAL_FIELDS:
            value = getattr(data, name)
            if value:
                body[name] = value
        # Pay with Wave API v1.3: a JSON body with amount and time_to_live_in_seconds
        # as JSON integers and items as a JSON string.
        return json_request(f"{self.config.base_url}/payment", body)

    def _redirect(self, data: WavePayPaymentData, response: GatewayResponse) -> RedirectPayment:
        body = response.json()
        transaction_id = get(body, "transaction_id")
        if not response.successful() or get(body, "message") != "success" or transaction_id == "":
            message = _error_message(body)
            raise ApiError(
                f"Wave Pay payment request failed with HTTP {response.status}: {message}",
                gateway_code="VALIDATION_ERROR" if "errors" in body else optional(body, "message"),
                gateway_message=message,
                http_status=response.status,
                raw=to_plain_object(body),
            )
        return RedirectPayment(
            order_id=data.order_id,
            url=f"{self.config.base_redirect_url}/authenticate?transaction_id="
            f"{query_escape(transaction_id)}",
            gateway_reference=transaction_id,
            raw=to_plain_object(body),
        )

    def _hash(self, parts: Sequence[str]) -> str:
        return hmac_sha256_hex(self.config.secret_key, "".join(parts))


def _error_message(body: LosslessObject) -> str:
    errors = body.get("errors")
    if isinstance(errors, dict):
        lines = []
        for name in sorted(errors):
            entries = errors[name]
            texts = [
                text
                for text in (
                    scalar_string(entry)
                    for entry in (entries if isinstance(entries, list) else [entries])
                )
                if text is not None
            ]
            lines.append(f"{name}: {' '.join(texts)}")
        return "; ".join(lines)
    return get(body, "message") or "unexpected response"


class WavePay(_WavePayBase, SyncGateway):
    """Wave Pay with a synchronous HTTP client.

    Redirect payments and callbacks. Wave has no status API, so the callback is
    the only result. Use :class:`AsyncWavePay` in async code.
    """

    def __init__(
        self,
        config: WavePayConfig | Mapping[str, Any],
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
    ) -> WavePay:
        """A gateway configured from the ``WAVE_PAY_*`` environment variables."""
        return cls(WavePayConfig.from_env(env), http_client=http_client)

    def initiate(self, data: WavePayPaymentData) -> RedirectPayment:
        """Creates a payment request and returns Wave's page to redirect the customer to.

        The request is a JSON post to ``{base_url}/payment`` (currency MMK); the
        customer approves it at ``{base_redirect_url}/authenticate``.
        Once ``data`` passes validation it fills ``data.merchant_reference_id``
        with a random ID when empty, so store it afterwards. Invalid data is left
        untouched.
        """
        request = self._initiate_request(data)
        return self._redirect(data, self._transport.send(request))


class AsyncWavePay(_WavePayBase, AsyncGateway):
    """Wave Pay with an async HTTP client. The same API as :class:`WavePay`, awaited."""

    def __init__(
        self,
        config: WavePayConfig | Mapping[str, Any],
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
    ) -> AsyncWavePay:
        """A gateway configured from the ``WAVE_PAY_*`` environment variables."""
        return cls(WavePayConfig.from_env(env), http_client=http_client)

    async def initiate(self, data: WavePayPaymentData) -> RedirectPayment:
        """Creates a payment request and returns Wave's page to redirect the customer to."""
        request = self._initiate_request(data)
        return self._redirect(data, await self._transport.send(request))
