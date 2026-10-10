"""The errors this package raises."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

__all__ = [
    "ApiError",
    "ConfigurationError",
    "InvalidPaymentDataError",
    "PaymentError",
    "SignatureVerificationError",
]


class PaymentError(Exception):
    """Base class of every error raised by this package."""


class InvalidPaymentDataError(PaymentError):
    """Raised when payment data has values the gateway would reject.

    Raised before any request is sent.
    """

    errors: Mapping[str, str]
    """Field name to error message, e.g. ``{"amount": "The amount field is required."}``."""

    def __init__(self, errors: Mapping[str, str]) -> None:
        messages = [errors[field] for field in sorted(errors)]
        super().__init__(f"Invalid payment data: {' '.join(messages)}")
        self.errors = MappingProxyType(dict(errors))


class ApiError(PaymentError):
    """Raised when a gateway rejects a request or answers with an error.

    This includes errors sent with HTTP 200, and gateways that cannot be reached
    (the network error is chained as ``__cause__``).
    """

    gateway_code: str | None
    """The gateway's own error code, e.g. ``ORDER_ID_USED`` or ``09``."""
    gateway_message: str | None
    """The gateway's own error message."""
    http_status: int
    """The HTTP status of the response, or ``0`` when no response was received."""
    raw: Mapping[str, Any]
    """The decoded response body."""

    def __init__(
        self,
        message: str,
        *,
        gateway_code: str | None = None,
        gateway_message: str | None = None,
        http_status: int = 0,
        raw: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.gateway_code = gateway_code
        self.gateway_message = gateway_message
        self.http_status = http_status
        self.raw = raw if raw is not None else {}


class SignatureVerificationError(PaymentError):
    """Raised when a callback, return redirect or gateway response fails verification.

    Never act on its payload.
    """

    raw: Mapping[str, Any]
    """The unverified payload, for logging only."""

    def __init__(self, message: str, raw: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.raw = raw if raw is not None else {}


class ConfigurationError(PaymentError):
    """Raised when a gateway is missing a credential or setting it needs.

    ``invalid`` reports a time setting that is set but not a whole number greater
    than 0.
    """

    gateway: str
    """The gateway, e.g. ``kbz_pay``."""
    key: str
    """The missing or invalid setting, e.g. ``app_key``."""

    def __init__(self, gateway: str, key: str, invalid: bool = False) -> None:
        if invalid:
            message = f"The {gateway} configuration [{key}] must be a whole number greater than 0."
        else:
            message = f"The {gateway} configuration is missing [{key}]."
        super().__init__(message)
        self.gateway = gateway
        self.key = key
