"""Turns a FastAPI request into the core ``CallbackRequest`` and verifies it."""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import Depends, HTTPException, Request
from python_myanmar_payments import CallbackRequest, PaymentCallback, SignatureVerificationError

__all__ = ["CallbackHandler", "CallbackRequestDep", "callback_request", "verify_callback"]


async def callback_request(request: Request) -> CallbackRequest:
    """The request exactly as the gateway sent it: raw body bytes, headers and query string.

    Nothing is parsed or re-encoded here, so JSON and form callbacks reach the
    core's signature check byte for byte. Use it as a dependency
    (:data:`CallbackRequestDep`) or await it in an endpoint.
    """
    return CallbackRequest(
        body=await request.body(),
        headers=request.headers,
        query=request.scope["query_string"],
    )


CallbackRequestDep = Annotated[CallbackRequest, Depends(callback_request)]


class CallbackHandler(Protocol):
    """Any gateway that verifies callbacks: sync or async clients, or ``CyberSource``."""

    def handle_callback(self, request: CallbackRequest) -> PaymentCallback: ...


def verify_callback(gateway: CallbackHandler, request: CallbackRequest) -> PaymentCallback:
    """Verifies a callback with the gateway's ``handle_callback()``.

    A ``SignatureVerificationError`` (a bad signature or a malformed signed
    callback) becomes an ``HTTPException(400)`` with a generic detail; the original
    error is chained as ``__cause__`` and its unverified payload is never sent back.
    Other errors propagate.
    """
    try:
        return gateway.handle_callback(request)
    except SignatureVerificationError as error:
        raise HTTPException(status_code=400, detail="Callback verification failed.") from error
