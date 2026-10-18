"""Creates the payments facade once per application and closes what it created."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from python_myanmar_payments import AsyncMyanmarPayments

__all__ = ["payments_lifespan"]


def payments_lifespan(
    payments: AsyncMyanmarPayments | Callable[[], AsyncMyanmarPayments],
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """A FastAPI lifespan that stores one ``AsyncMyanmarPayments`` on ``app.state``.

    Pass a factory such as ``AsyncMyanmarPayments.from_env`` to let the lifespan
    create the facade at startup and ``aclose()`` it at shutdown; that closes only
    the HTTP clients the facade created, never an ``http_client`` or token cache
    you gave it. Pass an instance to keep it under your control: it is never
    closed.

    .. code-block:: python

        app = FastAPI(lifespan=payments_lifespan(AsyncMyanmarPayments.from_env))
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if isinstance(payments, AsyncMyanmarPayments):
            instance, owned = payments, False
        else:
            instance, owned = payments(), True
        app.state.myanmar_payments = instance
        try:
            yield
        finally:
            del app.state.myanmar_payments
            if owned:
                await instance.aclose()

    return lifespan
