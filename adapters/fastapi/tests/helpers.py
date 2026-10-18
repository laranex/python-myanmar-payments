from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from python_myanmar_payments import AsyncMyanmarPayments

# The core SDK's shared test vectors (the same files as the PHP, Go and Node SDKs).
FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"

CONFIG: dict[str, Any] = {
    "kbz_pay": {
        "app_id": "kp123",
        "app_key": "secret-key",
        "merchant_code": "100001",
        "api_url": "https://api.kbzpay.com/payment/gateway",
        "pwa_url": "https://wap.kbzpay.com/pgw/pwa/#/",
        "time_to_live_minutes": 15,
    },
    "wave_pay": {
        "merchant_id": "testmerchantID",
        "secret_key": "test-secret",
        "merchant_name": "Shop",
        "base_url": "https://payments.wavemoney.io",
        "base_redirect_url": "https://payments.wavemoney.io",
        "time_to_live_seconds": 300,
    },
    "aya_pay": {
        "app_key": "app-key",
        "app_secret": "test-secret",
        "base_url": "https://pgw.ayainnovation.com",
    },
    "yoma_mmqr": {
        "merchant_id": "M001",
        "client_id": "client",
        "client_secret": "secret",
        "webhook_hash_key": "hash-key",
        "api_version": "v1rc",
        "webhook_secret": "webhook-secret",
        "base_url": "https://paymenthubapi.yomabank.com",
    },
    "cyber_source": {
        "profile_id": "profile",
        "access_key": "access",
        "secret_key": "cyber-secret",
        "base_url": "https://secureacceptance.cybersource.com",
    },
}


def fixture(path: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / path).read_text("utf-8"))
    return data


def hmac_hex(message: str, key: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def hmac_base64(message: str, key: str) -> str:
    digest = hmac.new(key.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class SpyPayments(AsyncMyanmarPayments):
    """An ``AsyncMyanmarPayments`` that counts ``aclose()`` calls."""

    closed = 0

    async def aclose(self) -> None:
        self.closed += 1
        await super().aclose()


def payments(**options: Any) -> SpyPayments:
    return SpyPayments(**CONFIG, **options)


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Runs the app's lifespan and an in-process client, like a server would."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
