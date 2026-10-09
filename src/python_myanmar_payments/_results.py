"""What initiating a payment, checking a status and verifying a callback return."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import Any, ClassVar, Literal

from ._status import PaymentFlow, PaymentStatus

__all__ = [
    "Acknowledgement",
    "AppPayment",
    "FormField",
    "FormPayment",
    "PaymentCallback",
    "PaymentResult",
    "PaymentStatusResult",
    "QrPayment",
    "RedirectPayment",
]


@dataclass(frozen=True, kw_only=True)
class RedirectPayment:
    """Send the customer's browser to ``url`` to complete the payment."""

    flow: ClassVar[Literal[PaymentFlow.REDIRECT]] = PaymentFlow.REDIRECT
    order_id: str
    """Your order ID, as sent to the gateway."""
    url: str
    """The gateway page to redirect the customer to."""
    gateway_reference: str | None = None
    """The gateway's ID for this attempt (KBZ ``prepay_id``, Wave ``transaction_id``)."""
    raw: Mapping[str, Any] = field(default_factory=dict)
    """The gateway's response, for logging."""


@dataclass(frozen=True)
class FormField:
    """One signed hidden field of a :class:`FormPayment`."""

    name: str
    value: str


@dataclass(frozen=True, kw_only=True)
class FormPayment:
    """POST ``fields`` to ``action`` from the customer's browser.

    :meth:`to_html` renders a page that does this automatically.
    """

    flow: ClassVar[Literal[PaymentFlow.FORM]] = PaymentFlow.FORM
    order_id: str
    """Your order ID, as sent to the gateway."""
    action: str
    """The gateway URL the form posts to."""
    fields: tuple[FormField, ...]
    """The signed hidden fields, in signing order. Post them unchanged."""
    enctype: str = "application/x-www-form-urlencoded"
    """The encoding the gateway expects for the form."""

    def field(self, name: str) -> str | None:
        """The value of the named field, or ``None``."""
        return next((item.value for item in self.fields if item.name == name), None)

    def values(self) -> dict[str, str]:
        """The fields as a dict, e.g. to render the form with your own template."""
        return {item.name: item.value for item in self.fields}

    def to_html(self) -> str:
        """A complete HTML page that posts the form as soon as it loads.

        Every value is escaped.
        """
        inputs = "".join(
            f'<input type="hidden" name="{_escape(item.name)}" value="{_escape(item.value)}">'
            for item in self.fields
        )
        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            "<title>Redirecting to payment</title></head><body>"
            f'<form id="payment-form" method="POST" action="{_escape(self.action)}" '
            f'enctype="{_escape(self.enctype)}">'
            f"{inputs}"
            '<noscript><button type="submit">Continue to payment</button></noscript></form>'
            '<script>document.getElementById("payment-form").submit();</script></body></html>'
        )


@dataclass(frozen=True, kw_only=True)
class QrPayment:
    """Show a QR code for the customer to scan.

    Gateways return either a payload to encode (``qr_string``) or a ready-made
    image (``qr_image``).
    """

    flow: ClassVar[Literal[PaymentFlow.QR]] = PaymentFlow.QR
    order_id: str
    """Your order ID, as sent to the gateway."""
    qr_string: str | None = None
    """A QR payload to encode into an image yourself (KBZ Pay)."""
    qr_image: str | None = None
    """A base64 encoded image to display as is (Yoma MMQR)."""
    expires_at: datetime | None = None
    """When the QR stops being payable (UTC), when the gateway limits it."""
    reference: str | None = None
    """The gateway's ID for this QR, used to check its status (Yoma ``refLabel``, KBZ
    ``prepay_id``)."""
    raw: Mapping[str, Any] = field(default_factory=dict)
    """The gateway's response, for logging."""

    def qr_image_data_uri(self, mime_type: str = "image/png") -> str | None:
        """The image as a data URI for an ``<img src>``, or ``None`` without an image."""
        return None if self.qr_image is None else f"data:{mime_type};base64,{self.qr_image}"


@dataclass(frozen=True, kw_only=True)
class AppPayment:
    """Pass these values to your mobile app, which hands them to the wallet's SDK.

    :meth:`to_dict` returns ``orderId``, ``orderInfo``, ``sign`` and ``signType``.
    """

    flow: ClassVar[Literal[PaymentFlow.APP]] = PaymentFlow.APP
    order_id: str
    """Your order ID, as sent to the gateway."""
    order_info: str
    """The signed order string the SDK expects."""
    sign: str
    """The signature of ``order_info``."""
    sign_type: str
    """The signature algorithm, e.g. ``SHA256``."""
    raw: Mapping[str, Any] = field(default_factory=dict)
    """The gateway's response, for logging. Left out of :meth:`to_dict`."""

    def to_dict(self) -> dict[str, str]:
        """The values your app needs: ``orderId``, ``orderInfo``, ``sign`` and ``signType``."""
        return {
            "orderId": self.order_id,
            "orderInfo": self.order_info,
            "sign": self.sign,
            "signType": self.sign_type,
        }


PaymentResult = RedirectPayment | FormPayment | QrPayment | AppPayment
"""Returned when a payment is initiated. Each flow has its own class; check ``flow``."""


@dataclass(frozen=True, kw_only=True)
class Acknowledgement:
    """The HTTP response a gateway expects after it delivers a callback.

    Gateways retry until they receive it.
    """

    status: int = 200
    """The HTTP status, 200 unless a gateway documents another."""
    body: str = ""
    """The response body, e.g. KBZ Pay's plain ``success``."""
    headers: Mapping[str, str] = field(default_factory=lambda: {"Content-Type": "text/plain"})
    """The response headers."""

    @classmethod
    def default(cls) -> Acknowledgement:
        """An empty ``200 text/plain`` response, which most gateways expect."""
        return cls()


class PaymentCallback:
    """A gateway notification whose signature has been verified.

    Always compare ``amount`` with your order before fulfilling it, and handle
    duplicates: gateways retry.
    """

    __slots__ = (
        "_acknowledgement",
        "amount",
        "gateway_reference",
        "gateway_status",
        "order_id",
        "raw",
        "status",
    )

    order_id: str
    """Your order ID."""
    status: PaymentStatus
    """The status mapped onto this package's statuses."""
    gateway_status: str
    """The gateway's own status value, unmapped."""
    gateway_reference: str | None
    """The gateway's ID for the payment, when it sends one."""
    amount: str | None
    """The amount the gateway reports, exactly as it sent it, when it sends one."""
    raw: Mapping[str, Any]
    """The verified payload."""

    def __init__(
        self,
        *,
        order_id: str,
        status: PaymentStatus,
        gateway_status: str,
        gateway_reference: str | None = None,
        amount: str | None = None,
        raw: Mapping[str, Any] | None = None,
        acknowledgement: Acknowledgement | None = None,
    ) -> None:
        self.order_id = order_id
        self.status = PaymentStatus(status)
        self.gateway_status = gateway_status
        self.gateway_reference = gateway_reference
        self.amount = amount
        self.raw = raw if raw is not None else {}
        self._acknowledgement = acknowledgement or Acknowledgement.default()

    def is_successful(self) -> bool:
        """Whether the customer paid."""
        return self.status is PaymentStatus.SUCCESSFUL

    def acknowledgement(self) -> Acknowledgement:
        """The response to send so the gateway stops retrying."""
        return self._acknowledgement

    def __repr__(self) -> str:
        return (
            f"PaymentCallback(order_id={self.order_id!r}, status={self.status.value!r}, "
            f"gateway_status={self.gateway_status!r}, "
            f"gateway_reference={self.gateway_reference!r}, amount={self.amount!r})"
        )


@dataclass(frozen=True, kw_only=True)
class PaymentStatusResult:
    """The state of a payment as reported by a gateway's status API."""

    status: PaymentStatus
    """The status mapped onto this package's statuses."""
    gateway_status: str
    """The gateway's own status value, unmapped."""
    order_id: str | None = None
    """Your order ID, when the gateway returns it."""
    gateway_reference: str | None = None
    """The gateway's ID for the payment, when it sends one."""
    amount: str | None = None
    """The amount the gateway reports, exactly as it sent it, when it sends one."""
    raw: Mapping[str, Any] = field(default_factory=dict)
    """The gateway's response, for logging."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", PaymentStatus(self.status))

    def is_successful(self) -> bool:
        """Whether the customer paid."""
        return self.status is PaymentStatus.SUCCESSFUL


def _escape(value: str) -> str:
    # Matches the Node SDK: &, <, >, " (as &#34;) and ' (as &#39;).
    return escape(value, quote=True).replace("&quot;", "&#34;").replace("&#x27;", "&#39;")
