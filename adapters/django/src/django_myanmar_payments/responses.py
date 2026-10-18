"""Turns the core ``Acknowledgement`` into a Django response."""

from __future__ import annotations

from django.http import HttpResponse
from python_myanmar_payments import Acknowledgement

__all__ = ["acknowledgement_response"]


def acknowledgement_response(acknowledgement: Acknowledgement) -> HttpResponse:
    """The response the gateway expects: its status, body and headers, unchanged.

    The gateway's ``Content-Type`` replaces Django's ``text/html`` default.
    """
    return HttpResponse(
        acknowledgement.body,
        status=acknowledgement.status,
        headers=dict(acknowledgement.headers),
    )
