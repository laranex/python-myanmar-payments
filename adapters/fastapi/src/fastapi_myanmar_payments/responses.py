"""Turns the core ``Acknowledgement`` into a Starlette response."""

from __future__ import annotations

from fastapi import Response
from python_myanmar_payments import Acknowledgement

__all__ = ["acknowledgement_response"]


def acknowledgement_response(acknowledgement: Acknowledgement) -> Response:
    """The response the gateway expects: its status, body and headers, unchanged.

    No media type is added, so the ``Content-Type`` is exactly the gateway's
    (e.g. ``text/plain`` without a charset).
    """
    return Response(
        content=acknowledgement.body,
        status_code=acknowledgement.status,
        headers=dict(acknowledgement.headers),
    )
