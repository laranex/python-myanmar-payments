"""Gateway-independent payment statuses and flows."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

__all__ = ["PaymentFlow", "PaymentStatus", "resolve_status"]


class _StrEnum(str, Enum):
    """A string enum whose ``str()`` and format are its value on every Python."""

    def __str__(self) -> str:
        return str(self.value)

    def __format__(self, format_spec: str) -> str:
        return format(str(self.value), format_spec)


class PaymentStatus(_StrEnum):
    """A gateway-independent payment status.

    Every gateway's own status values are mapped onto these. Members are strings,
    so ``callback.status == "successful"`` works too.
    """

    SUCCESSFUL = "successful"
    """The customer paid. The only status that means money was collected."""
    PENDING = "pending"
    """The payment is still in progress or waiting on the customer."""
    FAILED = "failed"
    """The payment was attempted and failed or was rejected."""
    CANCELED = "canceled"
    """The payment or order was canceled or closed before completing."""
    EXPIRED = "expired"
    """The payment window ran out before the customer paid."""
    UNKNOWN = "unknown"
    """The gateway sent a status this package does not recognize. Inspect ``gateway_status``."""

    def is_final(self) -> bool:
        """Whether the status will not change any more: ``False`` for pending and unknown."""
        return self not in (PaymentStatus.PENDING, PaymentStatus.UNKNOWN)


class PaymentFlow(_StrEnum):
    """How the customer completes a payment after it has been initiated."""

    REDIRECT = "redirect"
    """Send the customer's browser to a gateway-hosted URL."""
    FORM = "form"
    """POST a signed form from the customer's browser to the gateway."""
    QR = "qr"
    """Show a QR code the customer scans with their wallet app."""
    APP = "app"
    """Hand a signed payload to your mobile app, which opens the wallet SDK."""


def resolve_status(
    statuses: Mapping[str, PaymentStatus], gateway_status: str | None
) -> PaymentStatus:
    """Maps a gateway status onto a :class:`PaymentStatus`, trimming whitespace.

    Statuses missing from the map resolve to ``PaymentStatus.UNKNOWN``.
    """
    return statuses.get((gateway_status or "").strip(), PaymentStatus.UNKNOWN)
