from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import httpx

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


class FakeGateway:
    """Answers Wave Pay and Yoma MMQR requests and records every one."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.clients: list[httpx.Client | httpx.AsyncClient] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if request.url.path.endswith("/check-status"):
            return httpx.Response(200, json={"paymentStatus": "SUCCESS", "refLabel": "REF1"})
        return httpx.Response(200, json={"message": "success", "transaction_id": "T1"})

    def client(self) -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        self.clients.append(client)
        return client

    def async_client(self) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        self.clients.append(client)
        return client


gateway = FakeGateway()


def sync_client() -> httpx.Client:
    return gateway.client()


def async_client() -> httpx.AsyncClient:
    return gateway.async_client()


HTTP_CLIENTS = {
    "http_client": "tests.helpers.sync_client",
    "async_http_client": "tests.helpers.async_client",
}
