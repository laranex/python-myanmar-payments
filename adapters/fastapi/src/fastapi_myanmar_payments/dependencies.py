"""FastAPI dependencies that hand out the application's payments facade and gateways."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from python_myanmar_payments import (
    AsyncAyaPay,
    AsyncKbzPay,
    AsyncMyanmarPayments,
    AsyncWavePay,
    AsyncYomaMmqr,
    CyberSource,
)

__all__ = [
    "AyaPayDep",
    "CyberSourceDep",
    "KbzPayDep",
    "PaymentsDep",
    "WavePayDep",
    "YomaMmqrDep",
    "get_aya_pay",
    "get_cyber_source",
    "get_kbz_pay",
    "get_payments",
    "get_wave_pay",
    "get_yoma_mmqr",
]

# The dependencies are `async def` so FastAPI runs them on the event loop rather
# than in its thread pool, where two requests could build the same gateway twice.


async def get_payments(request: Request) -> AsyncMyanmarPayments:
    """The ``AsyncMyanmarPayments`` that :func:`payments_lifespan` stored on the app.

    Override it with ``app.dependency_overrides[get_payments]`` in tests; the
    gateway dependencies use it too.
    """
    payments = getattr(request.app.state, "myanmar_payments", None)
    if not isinstance(payments, AsyncMyanmarPayments):
        raise RuntimeError(
            "No AsyncMyanmarPayments is configured for this app: pass "
            "payments_lifespan(...) as the FastAPI lifespan or override get_payments."
        )
    return payments


PaymentsDep = Annotated[AsyncMyanmarPayments, Depends(get_payments)]


async def get_kbz_pay(payments: PaymentsDep) -> AsyncKbzPay:
    """The KBZ Pay gateway. Raises the core ``ConfigurationError`` when not configured."""
    return payments.kbz_pay()


async def get_wave_pay(payments: PaymentsDep) -> AsyncWavePay:
    """The Wave Pay gateway. Raises the core ``ConfigurationError`` when not configured."""
    return payments.wave_pay()


async def get_aya_pay(payments: PaymentsDep) -> AsyncAyaPay:
    """The AYA Payment Gateway. Raises the core ``ConfigurationError`` when not configured."""
    return payments.aya_pay()


async def get_yoma_mmqr(payments: PaymentsDep) -> AsyncYomaMmqr:
    """The Yoma MMQR gateway. Raises the core ``ConfigurationError`` when not configured."""
    return payments.yoma_mmqr()


async def get_cyber_source(payments: PaymentsDep) -> CyberSource:
    """The CyberSource gateway. Raises the core ``ConfigurationError`` when not configured."""
    return payments.cyber_source()


KbzPayDep = Annotated[AsyncKbzPay, Depends(get_kbz_pay)]
WavePayDep = Annotated[AsyncWavePay, Depends(get_wave_pay)]
AyaPayDep = Annotated[AsyncAyaPay, Depends(get_aya_pay)]
YomaMmqrDep = Annotated[AsyncYomaMmqr, Depends(get_yoma_mmqr)]
CyberSourceDep = Annotated[CyberSource, Depends(get_cyber_source)]
