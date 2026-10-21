"""Wave Pay Mini App (Wave Money JS SDK spec v1.8): access tokens, signed payments, callbacks.

The customer pays inside the WavePay app, on your web page loaded in its webview.
Your server covers three steps:

1. ``access_token()`` for the page's ``WaveJsSDK.authModule.loginWithAccessToken(token)``.
2. ``payment()`` signs the object the page passes to
   ``WaveJsSDK.paymentModule.makePayment(payload)``.
3. ``handle_callback()`` verifies the result Wave posts to your callback URL.
"""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, cast

import httpx

from ._amount import Amount, AmountInput, to_amount
from ._cache import AnyTokenCache, MemoryTokenCache, TokenCache, resolve
from ._callback import CallbackRequest, lossless_body
from ._errors import ApiError, SignatureVerificationError
from ._http import AsyncGateway, GatewayResponse, HttpRequest, SyncGateway, form_request
from ._json import to_plain_object
from ._results import MiniAppPayment, PaymentCallback
from ._status import PaymentStatus, resolve_status
from ._support import (
    DEFAULT_TIMEOUT_SECONDS,
    EnvSource,
    config_of,
    default_env,
    env_first,
    hmac_sha256_hex,
    random_hex,
    read_timeout,
    require_setting,
    safe_equal,
    sha256_hex,
    token_ttl,
    trim_url,
)
from ._validate import AmountRule, Validator
from ._values import ASCII_WHITESPACE, get, is_nested, optional, trimmed

__all__ = [
    "AsyncWavePayMiniApp",
    "WavePayMiniApp",
    "WavePayMiniAppConfig",
    "WavePayMiniAppItem",
    "WavePayMiniAppPaymentData",
    "WavePayMiniAppSigner",
]


class WavePayMiniAppConfig:
    """Wave Pay Mini App credentials and endpoint.

    Every setting except the HTTP timeout is required; a missing or blank one
    raises a :class:`~python_myanmar_payments.ConfigurationError`. The timeout
    defaults to :data:`~python_myanmar_payments.DEFAULT_TIMEOUT_SECONDS`.
    :attr:`PRODUCTION_URL` names production's API base URL.
    """

    PRODUCTION_URL: ClassVar[str] = "https://api.wavemoney.io:8100"

    client_id: str
    """The client ID Wave issued, sent to the token endpoint."""
    client_secret: str
    """The client secret Wave issued, sent to the token endpoint."""
    mini_app_id: str
    """The mini app ID Wave issued (``miniAppID`` in ``makePayment``)."""
    secret_key: str
    """The hash secret key Wave issued for payment and callback hashes."""
    base_url: str
    """The API base URL, e.g. :attr:`PRODUCTION_URL` or
    ``https://preprodnewapi.wavemoney.io``. Tokens are requested at its
    ``/auth/v1/external-utility/token``."""
    timeout_seconds: int
    """Seconds before the default HTTP client gives up on a request (default 30)."""

    def __init__(
        self,
        *,
        client_id: str | int = "",
        client_secret: str | int = "",
        mini_app_id: str | int = "",
        secret_key: str | int = "",
        base_url: str = "",
        timeout_seconds: int | str | None = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.client_id = require_setting("wave_pay_mini_app", "client_id", client_id)
        self.client_secret = require_setting("wave_pay_mini_app", "client_secret", client_secret)
        self.mini_app_id = require_setting("wave_pay_mini_app", "mini_app_id", mini_app_id)
        self.secret_key = require_setting("wave_pay_mini_app", "secret_key", secret_key)
        self.base_url = trim_url(require_setting("wave_pay_mini_app", "base_url", base_url))
        self.timeout_seconds = read_timeout("wave_pay_mini_app", timeout_seconds)

    @classmethod
    def from_env(cls, env: EnvSource | None = None) -> WavePayMiniAppConfig:
        """Reads the ``WAVE_PAY_MINI_APP_*`` environment variables.

        ``WAVE_PAY_MINI_APP_CLIENT_ID``, ``WAVE_PAY_MINI_APP_CLIENT_SECRET``,
        ``WAVE_PAY_MINI_APP_MINI_APP_ID``, ``WAVE_PAY_MINI_APP_SECRET_KEY``,
        ``WAVE_PAY_MINI_APP_BASE_URL`` and ``MYANMAR_PAYMENTS_HTTP_TIMEOUT``.
        Defaults to ``os.environ``.
        """
        env = default_env() if env is None else env
        return cls(
            client_id=env_first(env, "WAVE_PAY_MINI_APP_CLIENT_ID"),
            client_secret=env_first(env, "WAVE_PAY_MINI_APP_CLIENT_SECRET"),
            mini_app_id=env_first(env, "WAVE_PAY_MINI_APP_MINI_APP_ID"),
            secret_key=env_first(env, "WAVE_PAY_MINI_APP_SECRET_KEY"),
            base_url=env_first(env, "WAVE_PAY_MINI_APP_BASE_URL"),
            timeout_seconds=env_first(env, "MYANMAR_PAYMENTS_HTTP_TIMEOUT"),
        )

    def __repr__(self) -> str:
        return f"WavePayMiniAppConfig(mini_app_id={self.mini_app_id!r})"


@dataclass(frozen=True)
class WavePayMiniAppItem:
    """A line item of a mini app payment (``items`` in ``makePayment``)."""

    name: str
    """The item name."""
    amount: AmountInput
    """The item amount in whole kyat, e.g. ``Amount.kyat(1000)`` or ``1000``."""


@dataclass(kw_only=True)
class WavePayMiniAppPaymentData:
    """A Wave Pay Mini App payment: the values signed into Wave's ``makePayment`` payload.

    Wave only accepts whole kyat and documents no length limits.
    """

    order_id: str
    """Your order ID (``orderID``)."""
    callback_url: str
    """The URL Wave posts the result to (``callBackUrl``). It is signed into the
    hash, so verify the callback with this same URL."""
    items: Sequence[WavePayMiniAppItem] = field(default_factory=list)
    """The line items; at least one."""
    amount: AmountInput | None = None
    """The total in whole kyat. Leave it unset to charge the exact sum of the items."""
    merchant_reference_id: str | None = None
    """Your reference for this attempt (``merchantRefID``).

    ``payment()`` fills it with a random 32-character hex ID when ``None`` or
    empty, so store it afterwards: the callback's ``merchantRefId`` names the
    attempt that was paid."""


class WavePayMiniAppSigner:
    """Wave's mini app hash (Wave Money JS SDK spec v1.8).

    Lowercase hex HMAC-SHA256, keyed with the secret key, over
    ``<amount><orderID><miniAppID><merchantRefID><callBackUrl>`` with no
    separator. The payment and its callback use the same hash.
    """

    def __init__(self, secret_key: str) -> None:
        self._secret_key = secret_key

    def hash(
        self,
        amount: str,
        order_id: str,
        mini_app_id: str,
        merchant_reference_id: str,
        callback_url: str,
    ) -> str:
        """The hash; ``amount`` is its exact whole-number text, e.g. ``"5000"``."""
        return hmac_sha256_hex(
            self._secret_key,
            f"{amount}{order_id}{mini_app_id}{merchant_reference_id}{callback_url}",
        )

    def verify(
        self,
        hash_value: str,
        amount: str,
        order_id: str,
        mini_app_id: str,
        merchant_reference_id: str,
        callback_url: str,
    ) -> bool:
        """Whether ``hash_value`` is the expected hash.

        Compared in constant time, ignoring the case of the hex digits.
        """
        expected = self.hash(amount, order_id, mini_app_id, merchant_reference_id, callback_url)
        return safe_equal(expected, hash_value.lower())


_STATUSES: Mapping[str, PaymentStatus] = {
    "successful": PaymentStatus.SUCCESSFUL,
    "processing": PaymentStatus.PENDING,
    "failed": PaymentStatus.FAILED,
}

# The callback fields Wave documents. A nested value in any of them fails verification.
_CALLBACK_FIELDS = (
    "merchantRefId",
    "orderId",
    "amount",
    "msisdn",
    "hashValue",
    "transactionId",
    "transactionStatus",
)

_RULE = AmountRule("Wave Pay Mini App", 0)
_WHOLE = re.compile(r"[0-9]+")


class _WavePayMiniAppBase:
    """What the sync and async Wave Pay Mini App clients share."""

    TOKEN_CACHE_PREFIX: ClassVar[str] = "myanmar-payments.wave-pay-mini-app.token."
    """Prefix of the access token's cache key, the same in every Laranex SDK so
    services in different languages can share one cache. The key ends with the
    SHA-256 of ``<base url>|<client id>``."""

    config: WavePayMiniAppConfig
    """The configuration in use."""
    signer: WavePayMiniAppSigner
    """Computes and checks Wave's hash with the configured secret key."""

    def __init__(self, config: WavePayMiniAppConfig | Mapping[str, Any]) -> None:
        self.config = config_of(WavePayMiniAppConfig, config)
        self.signer = WavePayMiniAppSigner(self.config.secret_key)

    @staticmethod
    def resolved_amount(data: WavePayMiniAppPaymentData) -> Amount | None:
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
    def validate(cls, data: WavePayMiniAppPaymentData) -> None:
        """Checks the payment against Wave's documented rules.

        Fields are checked in this order: ``order_id``, ``callback_url``,
        ``items``, ``merchant_reference_id``, each item's name and amount, then
        ``amount``. Raises an :class:`~python_myanmar_payments.InvalidPaymentDataError`.
        """
        items = list(data.items) if isinstance(data.items, (list, tuple)) else []
        reference = data.merchant_reference_id
        validator = (
            Validator()
            .required("order_id", data.order_id)
            .required("callback_url", data.callback_url)
            .url("callback_url", data.callback_url)
            .when(len(items) == 0, "items", "The items field must have at least one item.")
            .string("merchant_reference_id", reference)
        )
        if isinstance(reference, str) and reference != "":
            validator.required("merchant_reference_id", reference)
        for index, item in enumerate(items):
            validator.required(f"items.{index}.name", getattr(item, "name", None)).amount(
                f"items.{index}.amount", getattr(item, "amount", None), _RULE
            )
        resolved = cls.resolved_amount(data)
        if not (len(items) > 0 and data.amount is None and resolved is None):
            validator.amount("amount", data.amount if data.amount is not None else resolved, _RULE)
        validator.validate()

    def payment(self, data: WavePayMiniAppPaymentData) -> MiniAppPayment:
        """Signs a payment for the page's ``WaveJsSDK.paymentModule.makePayment(payload)``.

        No request is sent: pass ``to_dict()`` (or ``to_json()``) to the page
        unchanged. Once ``data`` passes validation it fills
        ``data.merchant_reference_id`` with a random 32-character hex ID when
        ``None`` or empty, so store it afterwards. Invalid data is left untouched.
        """
        self.validate(data)
        if not data.merchant_reference_id:
            data.merchant_reference_id = random_hex()
        amount = cast("Amount", self.resolved_amount(data))  # validate() checked it.
        return MiniAppPayment(
            order_id=data.order_id,
            amount=amount,
            mini_app_id=self.config.mini_app_id,
            merchant_reference_id=data.merchant_reference_id,
            callback_url=data.callback_url,
            hash_value=self.signer.hash(
                str(amount),
                data.order_id,
                self.config.mini_app_id,
                data.merchant_reference_id,
                data.callback_url,
            ),
            items=tuple((item.name, Amount.of(item.amount)) for item in data.items),
        )

    def handle_callback(self, request: CallbackRequest, callback_url: str) -> PaymentCallback:
        """Verifies Wave's callback. Only ``successful`` means the customer paid.

        ``processing`` is pending and ``failed`` failed (matched trimmed and
        case-insensitively); anything else is unknown.

        The callback carries neither the mini app ID nor the callback URL, so pass
        the callback URL the payment was signed with: normally the URL this
        callback was posted to, without its query string. The mini app ID comes
        from the configuration. A nested value, a missing or non-string
        ``hashValue`` or a wrong hash fails with "Wave Pay Mini App callback hash
        verification failed."; a correctly hashed callback with an empty
        ``orderId`` or ``merchantRefId`` or an ``amount`` that is not a whole
        number fails with "Wave Pay Mini App callback is malformed.".
        ``merchantRefId`` is in ``raw``. Wave defines no response body: acknowledge
        with an empty 200.
        """
        payload = lossless_body(request)
        hash_value = payload.get("hashValue")
        order_id = get(payload, "orderId")
        reference_id = get(payload, "merchantRefId")
        amount = get(payload, "amount")

        if (
            any(is_nested(payload.get(name)) for name in _CALLBACK_FIELDS)
            or not isinstance(hash_value, str)
            or not self.signer.verify(
                hash_value, amount, order_id, self.config.mini_app_id, reference_id, callback_url
            )
        ):
            raise SignatureVerificationError(
                "Wave Pay Mini App callback hash verification failed.", to_plain_object(payload)
            )

        if order_id == "" or reference_id == "" or _WHOLE.fullmatch(amount) is None:
            raise SignatureVerificationError(
                "Wave Pay Mini App callback is malformed.", to_plain_object(payload)
            )

        gateway_status = trimmed(payload, "transactionStatus")
        return PaymentCallback(
            order_id=order_id,
            status=resolve_status(_STATUSES, gateway_status.lower()),
            gateway_status=gateway_status,
            gateway_reference=optional(payload, "transactionId"),
            amount=amount,
            raw=to_plain_object(payload),
        )

    def _token_request(self) -> HttpRequest:
        return form_request(
            f"{self.config.base_url}/auth/v1/external-utility/token",
            {
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "grant_type": "client_credentials",
            },
        )

    @staticmethod
    def _token(response: GatewayResponse) -> tuple[str, int]:
        """The access token and how many seconds to cache it."""
        body = response.json()
        token = get(body, "access_token")
        if not response.successful() or token == "":
            error_code = get(body, "error")
            message = get(body, "error_description") or get(body, "message")
            raise ApiError(
                f"Wave Pay Mini App token request failed: [{error_code}] {message}".rstrip(
                    ASCII_WHITESPACE
                )
                if error_code
                else f"Wave Pay Mini App token request failed with HTTP {response.status}.",
                gateway_code=error_code or None,
                gateway_message=message or None,
                http_status=response.status,
                raw=to_plain_object(body),
            )
        return token, token_ttl(get(body, "expires_in"))

    def _token_cache_key(self) -> str:
        digest = sha256_hex(f"{self.config.base_url}|{self.config.client_id}")
        return f"{self.TOKEN_CACHE_PREFIX}{digest}"


class WavePayMiniApp(_WavePayMiniAppBase, SyncGateway):
    """Wave Pay Mini App with a synchronous HTTP client.

    Access tokens, signed payments and callbacks. Wave has no status API, so the
    callback is the only result. Access tokens are cached in a
    :class:`~python_myanmar_payments.TokenCache`; share the gateway (or pass a
    shared cache) so they survive between calls. Use :class:`AsyncWavePayMiniApp`
    in async code.
    """

    def __init__(
        self,
        config: WavePayMiniAppConfig | Mapping[str, Any],
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
    ) -> WavePayMiniApp:
        """A gateway configured from the ``WAVE_PAY_MINI_APP_*`` environment variables."""
        return cls(
            WavePayMiniAppConfig.from_env(env),
            token_cache=token_cache,
            http_client=http_client,
        )

    def access_token(self) -> str:
        """An access token for the page's ``WaveJsSDK.authModule.loginWithAccessToken(token)``.

        Served from the cache, or requested with a form post of the client
        credentials to ``{base_url}/auth/v1/external-utility/token`` and kept for
        its ``expires_in`` minus a minute (at least a minute; an hour when Wave
        sends none). Never request it from the page: it needs the client secret.
        Raises an :class:`~python_myanmar_payments.ApiError` when the request fails
        or the response has no ``access_token``.
        """
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

    def forget_token(self) -> None:
        """Drops the cached access token, e.g. after rotating the client secret."""
        self._cache.delete(self._token_cache_key())


class AsyncWavePayMiniApp(_WavePayMiniAppBase, AsyncGateway):
    """Wave Pay Mini App with an async HTTP client.

    The same API as :class:`WavePayMiniApp`; ``access_token()`` and
    ``forget_token()`` are awaited, while ``payment()`` and ``handle_callback()``
    send no request and are not. ``token_cache`` takes a
    :class:`~python_myanmar_payments.TokenCache` or an
    :class:`~python_myanmar_payments.AsyncTokenCache`.
    """

    def __init__(
        self,
        config: WavePayMiniAppConfig | Mapping[str, Any],
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
    ) -> AsyncWavePayMiniApp:
        """A gateway configured from the ``WAVE_PAY_MINI_APP_*`` environment variables."""
        return cls(
            WavePayMiniAppConfig.from_env(env),
            token_cache=token_cache,
            http_client=http_client,
        )

    async def access_token(self) -> str:
        """An access token for the page's ``loginWithAccessToken``, cached like the sync one."""
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
            await resolve(self._cache.set(key, token, ttl))
            return token

    async def forget_token(self) -> None:
        """Drops the cached access token, e.g. after rotating the client secret."""
        await resolve(self._cache.delete(self._token_cache_key()))

    async def _cached_token(self, key: str) -> str | None:
        cached = await resolve(self._cache.get(key))
        return cached if isinstance(cached, str) and cached != "" else None
