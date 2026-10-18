"""Turns a Django request into the core ``CallbackRequest`` and verifies it."""

from __future__ import annotations

from typing import Protocol

from django.core.exceptions import SuspiciousOperation
from django.http import HttpRequest
from python_myanmar_payments import CallbackRequest, PaymentCallback, SignatureVerificationError

__all__ = ["CallbackHandler", "CallbackVerificationFailed", "callback_request", "verify_callback"]


class CallbackVerificationFailed(SuspiciousOperation):
    """A callback failed the gateway's verification.

    Django answers it with a 400 and logs it to
    ``django.security.CallbackVerificationFailed``. The core
    ``SignatureVerificationError`` is chained as ``__cause__``.
    """


def callback_request(request: HttpRequest) -> CallbackRequest:
    """The request exactly as the gateway sent it: raw body bytes, headers and query string.

    Nothing is parsed or re-encoded, so JSON and form callbacks reach the core's
    signature check byte for byte. Works in sync and async views.
    """
    return CallbackRequest(
        body=request.body,
        headers=request.headers,
        query=request.META.get("QUERY_STRING", ""),
    )


class CallbackHandler(Protocol):
    """Any gateway that verifies callbacks: sync or async clients, or ``CyberSource``."""

    def handle_callback(self, request: CallbackRequest) -> PaymentCallback: ...


def verify_callback(
    gateway: CallbackHandler, request: HttpRequest | CallbackRequest
) -> PaymentCallback:
    """Verifies a callback with the gateway's ``handle_callback()``.

    A ``SignatureVerificationError`` (a bad signature or a malformed signed
    callback) becomes :class:`CallbackVerificationFailed` with a generic message,
    so the unverified payload is never sent back. Other errors propagate.
    """
    if isinstance(request, HttpRequest):
        request = callback_request(request)
    try:
        return gateway.handle_callback(request)
    except SignatureVerificationError as error:
        raise CallbackVerificationFailed("Callback verification failed.") from error
