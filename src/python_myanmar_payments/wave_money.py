"""Wave Money (WavePay payment gateway): redirect payments and callbacks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import ClassVar

import httpx

from ._amount import Amount, AmountInput, to_amount
from ._callback import CallbackRequest, lossless_body
from ._errors import ApiError, SignatureVerificationError
from ._http import (
    DEFAULT_TIMEOUT,
    AsyncGateway,
    GatewayResponse,
    HttpRequest,
    SyncGateway,
    form_request,
)
from ._json import LosslessObject, dumps, to_plain_object
from ._results import PaymentCallback, RedirectPayment
from ._status import PaymentStatus, resolve_status
from ._support import (
    EnvSource,
    default_env,
    env_first,
    env_int,
    env_sandbox,
    hmac_sha256_hex,
    optional_setting,
    query_escape,
    random_hex,
    require_setting,
    safe_equal,
    trim_url,
)
from ._validate import AmountRule, Validator
from ._values import get, optional, scalar_string, trimmed

__all__ = [
    "AsyncWaveMoney",
    "WaveMoney",
    "WaveMoneyConfig",
    "WaveMoneyItem",
    "WaveMoneyPaymentData",
]


class WaveMoneyConfig:
    """Wave Money credentials and endpoints.

    A missing credential raises a :class:`~python_myanmar_payments.ConfigurationError`.
    """

    SANDBOX_URL: ClassVar[str] = "https://preprodpayments.wavemoney.io:8107"
    PRODUCTION_URL: ClassVar[str] = "https://payments.wavemoney.io"
    SANDBOX_AUTHENTICATE_URL: ClassVar[str] = "https://preprodpayments.wavemoney.io"
    PRODUCTION_AUTHENTICATE_URL: ClassVar[str] = "https://payments.wavemoney.io"
    DEFAULT_TIME_TO_LIVE_SECONDS: ClassVar[int] = 300

    merchant_id: str
    """The merchant ID Wave issued."""
    secret_key: str
    """The hash secret key Wave issued."""
    merchant_name: str
    """Your business name, shown on Wave's payment page."""
    time_to_live_seconds: int
    """Seconds the customer has to pay."""
    sandbox: bool
    """Whether the test environment is used."""
    base_url: str
    """The API base URL in use."""
    authenticate_url: str
    """The host the customer is redirected to. Wave serves it without the API port."""

    def __init__(
        self,
        *,
        merchant_id: str,
        secret_key: str,
        merchant_name: str,
        time_to_live_seconds: int | None = None,
        sandbox: bool = True,
        base_url: str | None = None,
        authenticate_url: str | None = None,
    ) -> None:
        self.merchant_id = require_setting("wave_money", "merchant_id", merchant_id)
        self.secret_key = require_setting("wave_money", "secret_key", secret_key)
        self.merchant_name = require_setting("wave_money", "merchant_name", merchant_name)
        ttl = time_to_live_seconds
        valid = isinstance(ttl, int) and not isinstance(ttl, bool) and ttl > 0
        self.time_to_live_seconds = (
            ttl if valid and ttl is not None else self.DEFAULT_TIME_TO_LIVE_SECONDS
        )
        self.sandbox = sandbox
        self.base_url = trim_url(
            optional_setting(base_url) or (self.SANDBOX_URL if sandbox else self.PRODUCTION_URL)
        )
        self.authenticate_url = trim_url(
            optional_setting(authenticate_url)
            or (self.SANDBOX_AUTHENTICATE_URL if sandbox else self.PRODUCTION_AUTHENTICATE_URL)
        )

    @classmethod
    def from_env(cls, env: EnvSource | None = None) -> WaveMoneyConfig:
        """Reads the ``WAVE_MONEY_*`` environment variables.

        ``WAVE_MONEY_MERCHANT_ID``, ``WAVE_MONEY_SECRET_KEY``,
        ``WAVE_MONEY_MERCHANT_NAME`` (falling back to ``APP_NAME``),
        ``WAVE_MONEY_TIME_TO_LIVE_IN_SECONDS``, ``WAVE_MONEY_SANDBOX``,
        ``WAVE_MONEY_BASE_URL`` and ``WAVE_MONEY_AUTHENTICATE_URL``. Defaults to
        ``os.environ``.
        """
        env = default_env() if env is None else env
        return cls(
            merchant_id=env_first(env, "WAVE_MONEY_MERCHANT_ID"),
            secret_key=env_first(env, "WAVE_MONEY_SECRET_KEY"),
            merchant_name=env_first(env, "WAVE_MONEY_MERCHANT_NAME", "APP_NAME"),
            time_to_live_seconds=env_int(env, "WAVE_MONEY_TIME_TO_LIVE_IN_SECONDS"),
            sandbox=env_sandbox(env, "WAVE_MONEY_SANDBOX"),
            base_url=env_first(env, "WAVE_MONEY_BASE_URL"),
            authenticate_url=env_first(env, "WAVE_MONEY_AUTHENTICATE_URL"),
        )

    def __repr__(self) -> str:
        return f"WaveMoneyConfig(merchant_id={self.merchant_id!r}, sandbox={self.sandbox!r})"


@dataclass(frozen=True)
class WaveMoneyItem:
    """A line item shown on Wave's payment page."""

    name: str
    """The item name."""
    amount: AmountInput
    """The item amount in whole kyat, e.g. ``Amount.kyat(1000)`` or ``1000``."""


@dataclass(kw_only=True)
class WaveMoneyPaymentData:
    """A Wave Money payment request. Wave only accepts whole kyat (MMK)."""

    order_id: str
    """Your order ID. One order can have several payment attempts."""
    callback_url: str
    """The URL Wave posts the result to (``backend_result_url``)."""
    return_url: str
    """Where Wave sends the customer back (``frontend_result_url``). Not proof of
    payment."""
    description: str
    """Shown to the customer."""
    items: Sequence[WaveMoneyItem] = field(default_factory=list)
    """The line items shown on Wave's page; at least one."""
    amount: AmountInput | None = None
    """The total in whole kyat. Leave it unset to charge the sum of the items."""
    merchant_reference_id: str | None = None
    """The unique ID of this attempt; Wave rejects a reused one.

    ``initiate()`` fills it with a random ID when empty, so store it afterwards:
    Wave's callback may omit ``orderId`` but always carries this."""


_STATUSES: Mapping[str, PaymentStatus] = {
    "PAYMENT_CONFIRMED": PaymentStatus.SUCCESSFUL,
    "INSUFFICIENT_BALANCE": PaymentStatus.PENDING,
    "ACCOUNT_LOCKED": PaymentStatus.FAILED,
    "BILL_COLLECTION_FAILED": PaymentStatus.FAILED,
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

_RULE = AmountRule("Wave Money", 0)


class _WaveMoneyBase:
    """What the sync and async Wave Money clients share."""

    config: WaveMoneyConfig
    """The configuration in use."""

    def __init__(self, config: WaveMoneyConfig) -> None:
        self.config = config

    @staticmethod
    def resolved_amount(data: WaveMoneyPaymentData) -> Amount | None:
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
    def validate(cls, data: WaveMoneyPaymentData) -> None:
        """Checks the request against Wave's documented rules.

        Raises an :class:`~python_myanmar_payments.InvalidPaymentDataError`.
        """
        items = list(data.items) if isinstance(data.items, (list, tuple)) else []
        validator = (
            Validator()
            .required("order_id", data.order_id)
            .required("callback_url", data.callback_url)
            .url("callback_url", data.callback_url)
            .required("return_url", data.return_url)
            .url("return_url", data.return_url)
            .required("description", data.description)
            .when(len(items) == 0, "items", "The items field must have at least one item.")
            .string("merchant_reference_id", data.merchant_reference_id)
        )
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
        missing, null or empty, because Wave marks ``orderId`` as optional.
        """
        payload = lossless_body(request)
        parts = []
        for name in _CALLBACK_FIELDS:
            text = scalar_string(payload.get(name))
            parts.append("null" if text is None else text)
        expected = self._hash(parts)
        hash_value = payload.get("hashValue")

        if not isinstance(hash_value, str) or not safe_equal(expected, hash_value.lower()):
            raise SignatureVerificationError(
                "Wave Money callback hash verification failed.", to_plain_object(payload)
            )

        gateway_status = trimmed(payload, "status")
        return PaymentCallback(
            order_id=get(payload, "orderId") or get(payload, "merchantReferenceId"),
            status=resolve_status(_STATUSES, gateway_status),
            gateway_status=gateway_status,
            gateway_reference=optional(payload, "transactionId"),
            amount=optional(payload, "amount"),
            raw=to_plain_object(payload),
        )

    def _initiate_request(self, data: WaveMoneyPaymentData) -> HttpRequest:
        self.validate(data)
        if not data.merchant_reference_id:
            data.merchant_reference_id = random_hex()

        amount = str(self.resolved_amount(data))
        ttl = str(self.config.time_to_live_seconds)
        # Wave documents items as [{"name": "...", "amount": 1000}] with a numeric
        # amount; the amount text is written as a JSON number without a float.
        items = ",".join(
            f'{{"name":{dumps(item.name)},"amount":{to_amount(item.amount)}}}'
            for item in data.items
        )
        return form_request(
            f"{self.config.base_url}/payment",
            {
                "time_to_live_in_seconds": ttl,
                "merchant_id": self.config.merchant_id,
                "order_id": data.order_id,
                "merchant_reference_id": data.merchant_reference_id,
                "frontend_result_url": data.return_url,
                "backend_result_url": data.callback_url,
                "amount": amount,
                "payment_description": data.description,
                "merchant_name": self.config.merchant_name,
                "items": f"[{items}]",
                "hash": self._hash(
                    [
                        ttl,
                        self.config.merchant_id,
                        data.order_id,
                        amount,
                        data.callback_url,
                        data.merchant_reference_id,
                    ]
                ),
            },
        )

    def _redirect(self, data: WaveMoneyPaymentData, response: GatewayResponse) -> RedirectPayment:
        body = response.json()
        transaction_id = get(body, "transaction_id")
        if not response.successful() or get(body, "message") != "success" or transaction_id == "":
            message = _error_message(body)
            raise ApiError(
                f"Wave Money payment request failed with HTTP {response.status}: {message}",
                gateway_code="VALIDATION_ERROR" if "errors" in body else optional(body, "message"),
                gateway_message=message,
                http_status=response.status,
                raw=to_plain_object(body),
            )
        return RedirectPayment(
            order_id=data.order_id,
            url=f"{self.config.authenticate_url}/authenticate?transaction_id="
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


class WaveMoney(_WaveMoneyBase, SyncGateway):
    """Wave Money with a synchronous HTTP client.

    Redirect payments and callbacks. Wave has no status API, so the callback is
    the only result. Use :class:`AsyncWaveMoney` in async code.
    """

    def __init__(
        self,
        config: WaveMoneyConfig,
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
    ) -> WaveMoney:
        """A gateway configured from the ``WAVE_MONEY_*`` environment variables."""
        return cls(WaveMoneyConfig.from_env(env), http_client=http_client, timeout=timeout)

    def initiate(self, data: WaveMoneyPaymentData) -> RedirectPayment:
        """Creates a payment request and returns Wave's page to redirect the customer to.

        Once ``data`` passes validation it fills ``data.merchant_reference_id``
        with a random ID when empty, so store it afterwards. Invalid data is left
        untouched.
        """
        request = self._initiate_request(data)
        return self._redirect(data, self._transport.send(request))


class AsyncWaveMoney(_WaveMoneyBase, AsyncGateway):
    """Wave Money with an async HTTP client. The same API as :class:`WaveMoney`, awaited."""

    def __init__(
        self,
        config: WaveMoneyConfig,
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
    ) -> AsyncWaveMoney:
        """A gateway configured from the ``WAVE_MONEY_*`` environment variables."""
        return cls(WaveMoneyConfig.from_env(env), http_client=http_client, timeout=timeout)

    async def initiate(self, data: WaveMoneyPaymentData) -> RedirectPayment:
        """Creates a payment request and returns Wave's page to redirect the customer to."""
        request = self._initiate_request(data)
        return self._redirect(data, await self._transport.send(request))
