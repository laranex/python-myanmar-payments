"""FastAPI integration for python-myanmar-payments.

A lifespan that owns one ``AsyncMyanmarPayments``, dependencies for its gateways,
raw callback requests, verification that answers bad signatures with a 400, and
the gateway's acknowledgement as a response. Payments, signing and verification
stay in ``python_myanmar_payments``.
"""

from ._version import __version__
from .callbacks import CallbackHandler, CallbackRequestDep, callback_request, verify_callback
from .dependencies import (
    AyaPayDep,
    CyberSourceDep,
    KbzPayDep,
    PaymentsDep,
    WavePayDep,
    YomaMmqrDep,
    get_aya_pay,
    get_cyber_source,
    get_kbz_pay,
    get_payments,
    get_wave_pay,
    get_yoma_mmqr,
)
from .lifespan import payments_lifespan
from .responses import acknowledgement_response

__all__ = [
    "AyaPayDep",
    "CallbackHandler",
    "CallbackRequestDep",
    "CyberSourceDep",
    "KbzPayDep",
    "PaymentsDep",
    "WavePayDep",
    "YomaMmqrDep",
    "__version__",
    "acknowledgement_response",
    "callback_request",
    "get_aya_pay",
    "get_cyber_source",
    "get_kbz_pay",
    "get_payments",
    "get_wave_pay",
    "get_yoma_mmqr",
    "payments_lifespan",
    "verify_callback",
]
