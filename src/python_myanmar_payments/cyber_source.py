"""CyberSource Secure Acceptance hosted checkout for card payments."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from ._amount import AmountInput, to_amount
from ._callback import CallbackRequest, lossless_input
from ._errors import SignatureVerificationError
from ._json import LosslessObject, to_plain_object
from ._results import FormField, FormPayment, PaymentCallback
from ._status import PaymentStatus, _StrEnum, resolve_status
from ._support import (
    EnvSource,
    config_of,
    default_env,
    env_first,
    hmac_sha256_base64,
    optional_setting,
    random_hex,
    require_setting,
    safe_equal,
    trim_url,
    utc_now,
)
from ._validate import AmountRule, Validator
from ._values import get, optional, scalar_string, trimmed

__all__ = [
    "CyberSource",
    "CyberSourceConfig",
    "CyberSourcePaymentData",
    "CyberSourceTransactionType",
]


class CyberSourceConfig:
    """A CyberSource Secure Acceptance profile.

    Every setting except the URL override is required; a missing one raises a
    :class:`~python_myanmar_payments.ConfigurationError`. The URL defaults to
    production.
    """

    PRODUCTION_URL: ClassVar[str] = "https://secureacceptance.cybersource.com"

    profile_id: str
    """The Secure Acceptance profile ID."""
    access_key: str
    """The profile's access key."""
    secret_key: str
    """The profile's secret key, which signs the fields."""
    base_url: str
    """The base URL in use."""

    def __init__(
        self,
        *,
        profile_id: str = "",
        access_key: str = "",
        secret_key: str = "",
        base_url: str | None = None,
    ) -> None:
        self.profile_id = require_setting("cyber_source", "profile_id", profile_id)
        self.access_key = require_setting("cyber_source", "access_key", access_key)
        self.secret_key = require_setting("cyber_source", "secret_key", secret_key)
        self.base_url = trim_url(optional_setting(base_url) or self.PRODUCTION_URL)

    @classmethod
    def from_env(cls, env: EnvSource | None = None) -> CyberSourceConfig:
        """Reads the ``CYBER_SOURCE_*`` environment variables.

        ``CYBER_SOURCE_PROFILE_ID``, ``CYBER_SOURCE_ACCESS_KEY``,
        ``CYBER_SOURCE_SECRET_KEY`` and ``CYBER_SOURCE_BASE_URL``. Defaults to
        ``os.environ``.
        """
        env = default_env() if env is None else env
        return cls(
            profile_id=env_first(env, "CYBER_SOURCE_PROFILE_ID"),
            access_key=env_first(env, "CYBER_SOURCE_ACCESS_KEY"),
            secret_key=env_first(env, "CYBER_SOURCE_SECRET_KEY"),
            base_url=env_first(env, "CYBER_SOURCE_BASE_URL"),
        )

    def __repr__(self) -> str:
        return f"CyberSourceConfig(profile_id={self.profile_id!r})"


class CyberSourceTransactionType(_StrEnum):
    """What CyberSource does with the card."""

    SALE = "sale"
    """Authorize and capture in one step."""
    AUTHORIZATION = "authorization"
    """Authorize only; capture later."""
    SALE_AND_CREATE_TOKEN = "sale,create_payment_token"
    """A sale that also saves the card as a payment token."""
    AUTHORIZATION_AND_CREATE_TOKEN = "authorization,create_payment_token"
    """An authorization that also saves the card as a payment token."""


_TRANSACTION_TYPES = frozenset(kind.value for kind in CyberSourceTransactionType)


@dataclass(kw_only=True)
class CyberSourcePaymentData:
    """A Secure Acceptance card payment. CyberSource is multi-currency."""

    order_id: str
    """Your order ID (``reference_number``), at most 50 characters. Echoed back as
    ``req_reference_number``."""
    amount: AmountInput
    """The total in ``currency``: 0 or more, any decimals, at most 15 characters, e.g.
    ``Amount.parse("10.50")``."""
    callback_url: str
    """The URL CyberSource posts the result to (``override_backoffice_post_url``), at
    most 255 characters."""
    currency: str
    """An ISO 4217 code, e.g. ``MMK``."""
    transaction_type: CyberSourceTransactionType | str
    """What to do with the card, e.g. ``CyberSourceTransactionType.SALE``."""
    locale: str
    """The hosted page language, e.g. ``en-us``."""
    return_url: str | None = None
    """The receipt page (``override_custom_receipt_page``), at most 255 characters."""
    cancel_url: str | None = None
    """The page shown on cancel (``override_custom_cancel_page``), at most 255
    characters."""


_SIGNED_FIELDS = (
    "access_key",
    "profile_id",
    "transaction_uuid",
    "signed_field_names",
    "signed_date_time",
    "locale",
    "transaction_type",
    "reference_number",
    "amount",
    "currency",
    "override_custom_receipt_page",
    "override_backoffice_post_url",
    "override_custom_cancel_page",
)

_STATUSES: Mapping[str, PaymentStatus] = {
    "ACCEPT": PaymentStatus.SUCCESSFUL,
    "REVIEW": PaymentStatus.PENDING,
    "DECLINE": PaymentStatus.FAILED,
    "ERROR": PaymentStatus.FAILED,
    "CANCEL": PaymentStatus.CANCELED,
}

_CURRENCY = re.compile(r"[A-Z]{3}")
_LOCALE = re.compile(r"[a-z]{2}-[a-z]{2}")


class CyberSource:
    """CyberSource Secure Acceptance hosted checkout for card payments.

    Signs the form and verifies the result posts. Makes no network calls, so the
    same class serves sync and async code.
    """

    config: CyberSourceConfig
    """The configuration in use."""

    def __init__(self, config: CyberSourceConfig | Mapping[str, Any]) -> None:
        self.config = config_of(CyberSourceConfig, config)

    @classmethod
    def from_env(cls, env: EnvSource | None = None) -> CyberSource:
        """A gateway configured from the ``CYBER_SOURCE_*`` environment variables."""
        return cls(CyberSourceConfig.from_env(env))

    @staticmethod
    def validate(data: CyberSourcePaymentData) -> None:
        """Checks the payment against the Secure Acceptance field reference.

        Raises an :class:`~python_myanmar_payments.InvalidPaymentDataError`.
        """
        (
            Validator()
            .required("order_id", data.order_id)
            .max("order_id", data.order_id, 50)
            .amount(
                "amount",
                data.amount,
                AmountRule("CyberSource", None, max_length=15, allow_zero=True),
            )
            .required("callback_url", data.callback_url)
            .url("callback_url", data.callback_url)
            .max("callback_url", data.callback_url, 255)
            .string("return_url", data.return_url)
            .url("return_url", data.return_url)
            .max("return_url", data.return_url, 255)
            .string("cancel_url", data.cancel_url)
            .url("cancel_url", data.cancel_url)
            .max("cancel_url", data.cancel_url, 255)
            .required("currency", data.currency)
            .pattern("currency", data.currency, _CURRENCY, "a three letter ISO 4217 code")
            .required("transaction_type", data.transaction_type)
            .required("locale", data.locale)
            .pattern("locale", data.locale, _LOCALE, "a locale code such as en-us")
            .when(
                str(data.transaction_type) not in _TRANSACTION_TYPES,
                "transaction_type",
                "The transaction_type field is not a supported transaction type.",
            )
            .validate()
        )

    def initiate(self, data: CyberSourcePaymentData) -> FormPayment:
        """Signs the payment fields.

        The customer's browser must POST the returned form to CyberSource;
        ``to_html()`` renders a page that does it.
        """
        self.validate(data)

        values = {
            "access_key": self.config.access_key,
            "profile_id": self.config.profile_id,
            "transaction_uuid": random_hex(),
            "signed_field_names": ",".join(_SIGNED_FIELDS),
            "signed_date_time": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "locale": data.locale,
            "transaction_type": str(data.transaction_type),
            "reference_number": data.order_id,
            "amount": str(to_amount(data.amount)),
            "currency": data.currency,
            "override_custom_receipt_page": data.return_url or "",
            "override_backoffice_post_url": data.callback_url,
            "override_custom_cancel_page": data.cancel_url or "",
        }
        fields = [FormField(name, values[name]) for name in _SIGNED_FIELDS]
        fields.append(FormField("signature", self._sign(values) or ""))

        return FormPayment(
            order_id=data.order_id,
            action=f"{self.config.base_url}/pay",
            fields=tuple(fields),
        )

    def handle_callback(self, request: CallbackRequest) -> PaymentCallback:
        """Verifies CyberSource's result post.

        The same check works for the browser post to your receipt page. Only the
        fields listed in ``signed_field_names`` are trusted: ``decision`` and
        ``req_reference_number`` must be signed, and unsigned fields are left out
        of the result and ``raw``.
        """
        payload = lossless_input(request)
        expected = self._sign(payload)
        names = _signed_names(payload)
        if (
            expected is None
            or not safe_equal(expected, get(payload, "signature"))
            or "decision" not in names
            or "req_reference_number" not in names
        ):
            raise SignatureVerificationError(
                "CyberSource callback signature verification failed.", to_plain_object(payload)
            )

        # A signature only covers the listed fields, so anything else in the post
        # could have been added by the sender (e.g. a `decision` next to the signed
        # request fields).
        signed: LosslessObject = {name: payload.get(name) for name in [*names, "signature"]}

        decision = trimmed(signed, "decision").upper()
        return PaymentCallback(
            order_id=get(signed, "req_reference_number"),
            status=resolve_status(_STATUSES, decision),
            gateway_status=decision,
            gateway_reference=optional(signed, "transaction_id"),
            amount=get(signed, "auth_amount") or optional(signed, "req_amount"),
            raw=to_plain_object(signed),
        )

    def _sign(self, fields: Mapping[str, object]) -> str | None:
        """Signs the fields listed in ``signed_field_names``; ``None`` when one is missing."""
        pairs = []
        for name in _signed_names(fields):
            value = scalar_string(fields.get(name))
            if value is None:
                return None
            pairs.append(f"{name}={value}")
        if not pairs:
            return None
        return hmac_sha256_base64(self.config.secret_key, ",".join(pairs))


def _signed_names(fields: Mapping[str, object]) -> list[str]:
    """The names listed in ``signed_field_names``, in order."""
    return [name for name in get(fields, "signed_field_names").split(",") if name != ""]
