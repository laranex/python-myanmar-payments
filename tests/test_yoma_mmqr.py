from __future__ import annotations

import asyncio
import base64
import threading
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from python_myanmar_payments import (
    Amount,
    ApiError,
    AsyncYomaMmqr,
    CallbackRequest,
    ConfigurationError,
    InvalidPaymentDataError,
    MemoryTokenCache,
    PaymentStatus,
    SignatureVerificationError,
    YomaMmqr,
    YomaMmqrConfig,
    YomaMmqrPaymentData,
)
from tests.conftest import Clock
from tests.helpers import NOW, FakeServer, hmac_hex, make, run

CONFIG: dict[str, Any] = {
    "merchant_id": "M1",
    "client_id": "client",
    "client_secret": "secret",
    "webhook_hash_key": "hash-key",
    "api_version": "v1rc",
    "timeout_seconds": 30,
    "base_url": "https://yoma.test",
}
API = "https://yoma.test/payment-gateway/v1rc/api"
TOKEN = {"access_token": "TOKEN", "expires_in": 3600}
QR = {"qrString": "iVBORw0KGgo=", "refLabel": "REF1"}


def gateway(mode: str, server: FakeServer | None = None, **options: Any) -> Any:
    return make(mode, YomaMmqr, AsyncYomaMmqr, YomaMmqrConfig(**CONFIG), server, **options)


def data(**changes: Any) -> YomaMmqrPaymentData:
    values: dict[str, Any] = {"order_id": "ORDER_1", "amount": 1000, "description": "Order #1"}
    return YomaMmqrPaymentData(**{**values, **changes})


def callback_body(order: str = "ORDER_1", status: str = "SUCCESS", key: str = "hash-key") -> Any:
    digest = hmac_hex(f"orderNumber={order}&status={status}", order + key)
    return {"orderNumber": order, "status": status, "hashValue": digest}


class AsyncCache:
    """An async token cache, like one on redis.asyncio."""

    def __init__(self) -> None:
        self.items: dict[str, str] = {}
        self.ttls: list[int] = []

    async def get(self, key: str) -> str | None:
        return self.items.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self.items[key] = value
        self.ttls.append(ttl_seconds)

    async def delete(self, key: str) -> None:
        self.items.pop(key, None)


class TestYomaMmqr:
    def test_checks_out_and_generates_a_qr(self, mode: str, clock: Clock) -> None:
        server = FakeServer(TOKEN, {"checkOutStatus": True}, QR)
        payment = run(gateway(mode, server).initiate(data(amount=Amount.parse("1000"))))

        token, checkout, generate = server.requests
        assert token.url == "https://yoma.test/token"
        assert token.form() == {"grant_type": "client_credentials"}
        assert (
            token.headers["authorization"] == "Basic " + base64.b64encode(b"client:secret").decode()
        )
        assert checkout.url == f"{API}/payment/checkout"
        assert checkout.headers["authorization"] == "Bearer TOKEN"
        assert checkout.json() == {
            "merchantId": "M1",
            "orderNumber": "ORDER_1",
            "amount": "1000",
            "description": "Order #1",
        }
        assert generate.url == f"{API}/qr/generate"
        assert generate.json() == {"merchantId": "M1", "orderNumber": "ORDER_1"}

        assert payment.flow == "qr"
        assert payment.qr_image == "iVBORw0KGgo="
        assert payment.qr_image_data_uri() == "data:image/png;base64,iVBORw0KGgo="
        assert payment.qr_image_data_uri("image/jpeg") == "data:image/jpeg;base64,iVBORw0KGgo="
        assert payment.reference == "REF1"
        assert payment.expires_at == datetime.fromtimestamp(NOW + 120, tz=timezone.utc)

    def test_fails_when_the_checkout_is_not_confirmed(self, mode: str) -> None:
        server = FakeServer(TOKEN, {"checkOutStatus": "true"})
        with pytest.raises(ApiError, match="did not confirm the checkout"):
            run(gateway(mode, server).initiate(data()))

    def test_fails_when_no_qr_comes_back(self, mode: str) -> None:
        server = FakeServer(TOKEN, {"qrString": "x"})
        with pytest.raises(ApiError, match="did not return a QR"):
            run(gateway(mode, server).renew_qr("ORDER_1"))

    def test_reuses_the_cached_token(self, mode: str) -> None:
        server = FakeServer(TOKEN, QR, QR)
        yoma = gateway(mode, server)
        run(yoma.renew_qr("ORDER_1"))
        run(yoma.renew_qr("ORDER_1"))
        assert [request.path for request in server.requests].count("/token") == 1

    def test_refreshes_the_token_once_after_a_401(self, mode: str) -> None:
        server = FakeServer(TOKEN, (401, {}), {**TOKEN, "access_token": "NEW"}, QR)
        payment = run(gateway(mode, server).renew_qr("ORDER_1"))
        assert payment.reference == "REF1"
        assert server.last().headers["authorization"] == "Bearer NEW"

    def test_forgets_the_token(self, mode: str) -> None:
        server = FakeServer(TOKEN, QR, TOKEN, QR)
        yoma = gateway(mode, server)
        run(yoma.renew_qr("ORDER_1"))
        run(yoma.forget_token())
        run(yoma.renew_qr("ORDER_1"))
        assert [request.path for request in server.requests].count("/token") == 2

    @pytest.mark.parametrize(
        ("expires_in", "ttl"),
        [(3600, 3540), ("7200", 7140), ("100.5", 60), (0, 3540), ("abc", 3540), (None, 3540)],
    )
    def test_caches_the_token_for_its_lifetime_minus_a_minute(
        self, expires_in: Any, ttl: int
    ) -> None:
        cache = AsyncCache()
        server = FakeServer({"access_token": "T", "expires_in": expires_in}, QR)
        run(gateway("async", server, token_cache=cache).renew_qr("ORDER_1"))
        assert cache.ttls == [ttl]
        assert list(cache.items.values()) == ["T"]
        key = next(iter(cache.items))
        assert key.startswith("myanmar-payments.yoma-mmqr.token.")

    def test_shares_a_cache_between_gateways(self) -> None:
        cache = MemoryTokenCache()
        server = FakeServer(TOKEN, QR, QR)
        run(gateway("sync", server, token_cache=cache).renew_qr("ORDER_1"))
        run(gateway("async", server, token_cache=cache).renew_qr("ORDER_1"))
        assert [request.path for request in server.requests].count("/token") == 1

    @pytest.mark.parametrize(
        ("reply", "message", "code"),
        [
            (
                (401, {"error": "invalid_client", "error_description": "Bad client"}),
                "Yoma MMQR token failed: [invalid_client] Bad client",
                "invalid_client",
            ),
            ((500, "down"), "Yoma MMQR token failed with HTTP 500.", None),
            ((200, {"token_type": "bearer"}), "Yoma MMQR token failed with HTTP 200.", None),
        ],
    )
    def test_raises_token_errors(
        self, mode: str, reply: Any, message: str, code: str | None
    ) -> None:
        with pytest.raises(ApiError) as info:
            run(gateway(mode, FakeServer(reply)).renew_qr("ORDER_1"))
        assert str(info.value) == message
        assert info.value.gateway_code == code

    @pytest.mark.parametrize(
        ("reply", "message", "code"),
        [
            (
                (200, {"errorCode": "E01", "errorDescription": "Duplicate order"}),
                "Yoma MMQR payment/checkout failed: [E01] Duplicate order",
                "E01",
            ),
            ((200, {"errorCode": "E02"}), "Yoma MMQR payment/checkout failed: [E02]", "E02"),
            ((502, {}), "Yoma MMQR payment/checkout failed with HTTP 502.", None),
        ],
    )
    def test_raises_api_errors(self, mode: str, reply: Any, message: str, code: str | None) -> None:
        with pytest.raises(ApiError) as info:
            run(gateway(mode, FakeServer(TOKEN, reply)).initiate(data()))
        assert str(info.value) == message
        assert info.value.gateway_code == code
        assert info.value.gateway_message in ("Duplicate order", None)

    @pytest.mark.parametrize(
        ("payment_status", "status"),
        [
            ("SUCCESS", PaymentStatus.SUCCESSFUL),
            ("success", PaymentStatus.SUCCESSFUL),
            ("PENDING", PaymentStatus.PENDING),
            ("FAILED", PaymentStatus.FAILED),
            ("FAIL", PaymentStatus.FAILED),
            ("REFUNDED", PaymentStatus.UNKNOWN),
        ],
    )
    def test_checks_the_status(self, mode: str, payment_status: str, status: Any) -> None:
        server = FakeServer(TOKEN, {"paymentStatus": payment_status, "refLabel": "REF1"})
        result = run(gateway(mode, server).status("REF1"))
        assert server.last().url == f"{API}/payment/check-status"
        assert server.last().json() == {"merchantId": "M1", "refLabel": "REF1"}
        assert result.status is status
        assert result.gateway_status == payment_status
        assert result.gateway_reference == "REF1"
        assert result.order_id is None

    def test_keeps_the_queried_reference(self, mode: str) -> None:
        server = FakeServer(TOKEN, {"paymentStatus": "PENDING"})
        assert run(gateway(mode, server).status("REF9")).gateway_reference == "REF9"

    def test_reports_an_expired_qr(self, mode: str) -> None:
        server = FakeServer(TOKEN, {"errorCode": "QR EXPIRED", "errorDescription": "Expired"})
        result = run(gateway(mode, server).status("REF1"))
        assert result.status is PaymentStatus.EXPIRED
        assert result.gateway_status == "QR EXPIRED"
        assert result.gateway_reference == "REF1"
        assert result.raw == {"errorCode": "QR EXPIRED", "errorDescription": "Expired"}

    def test_requests_one_token_for_concurrent_async_calls(self) -> None:
        server = FakeServer(TOKEN, *([QR] * 10))
        yoma = gateway("async", server)

        async def many() -> None:
            await asyncio.gather(*(yoma.renew_qr("ORDER_1") for _ in range(10)))

        asyncio.run(many())
        assert [request.path for request in server.requests].count("/token") == 1

    def test_uses_a_token_another_caller_cached_while_it_waited(self, mode: str) -> None:
        class RacingCache(MemoryTokenCache):
            def __init__(self) -> None:
                super().__init__()
                self.reads = 0

            def get(self, key: str) -> str | None:
                self.reads += 1
                return None if self.reads == 1 else "OTHER"

        server = FakeServer(QR)
        run(gateway(mode, server, token_cache=RacingCache()).renew_qr("ORDER_1"))
        assert [request.path for request in server.requests] == [
            "/payment-gateway/v1rc/api/qr/generate"
        ]
        assert server.last().headers["authorization"] == "Bearer OTHER"

    def test_requests_one_token_for_concurrent_threads(self) -> None:
        lock = threading.Lock()
        tokens = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal tokens
            if request.url.path == "/token":
                with lock:
                    tokens += 1
                return httpx.Response(200, json=TOKEN)
            return httpx.Response(200, json=QR)

        yoma = YomaMmqr(
            YomaMmqrConfig(**CONFIG),
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        threads = [threading.Thread(target=yoma.renew_qr, args=("ORDER_1",)) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert tokens == 1

    def test_verifies_a_callback(self, mode: str) -> None:
        callback = gateway(mode).handle_callback(CallbackRequest.from_json(callback_body()))
        assert callback.is_successful()
        assert callback.order_id == "ORDER_1"
        assert callback.gateway_status == "SUCCESS"
        assert callback.gateway_reference is None
        assert callback.amount is None

    def test_trims_the_status_and_compares_the_hash_case_insensitively(self) -> None:
        body = callback_body(status="failed")
        body["status"] = " failed "
        body["hashValue"] = body["hashValue"].upper()
        callback = gateway("sync").handle_callback(CallbackRequest.from_json(body))
        assert callback.status is PaymentStatus.FAILED
        assert callback.gateway_status == "failed"

    def test_rejects_a_bad_callback(self) -> None:
        tampered = {**callback_body(), "status": "FAILED"}
        for body in (tampered, callback_body(order=""), {}):
            with pytest.raises(SignatureVerificationError, match="hash verification failed"):
                gateway("sync").handle_callback(CallbackRequest.from_json(body))

    def test_checks_the_webhook_secret_header(self) -> None:
        yoma = YomaMmqr(YomaMmqrConfig(**CONFIG, webhook_secret="shared"))
        good = CallbackRequest.from_json(callback_body(), {"X-Webhook-Secret": "shared"})
        assert yoma.handle_callback(good).order_id == "ORDER_1"
        for headers in ({"X-Webhook-Secret": "wrong"}, None):
            request = CallbackRequest.from_json(callback_body(), headers)
            with pytest.raises(SignatureVerificationError, match="X-Webhook-Secret"):
                yoma.handle_callback(request)

    @pytest.mark.parametrize(
        ("change", "field"),
        [
            ({"order_id": ""}, "order_id"),
            ({"order_id": "O" * 21}, "order_id"),
            ({"amount": "1.5"}, "amount"),
            ({"amount": Amount.kyat(0)}, "amount"),
            ({"description": ""}, "description"),
            ({"description": "d" * 51}, "description"),
        ],
    )
    def test_validates_against_yoma_limits(self, change: Any, field: str) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            YomaMmqr.validate(data(**change))
        assert info.value.errors[field]


class TestYomaMmqrConfig:
    def test_defaults_to_the_production_endpoint(self) -> None:
        values = {key: value for key, value in CONFIG.items() if key != "base_url"}
        config = YomaMmqrConfig(**values)
        assert config.base_url == YomaMmqrConfig.PRODUCTION_URL
        assert config.api_version == "v1rc"
        assert config.timeout_seconds == 30
        assert config.webhook_secret is None
        assert repr(config) == "YomaMmqrConfig(merchant_id='M1')"

    def test_requires_the_api_version(self) -> None:
        with pytest.raises(ConfigurationError) as info:
            YomaMmqrConfig(**{**CONFIG, "api_version": " "})
        assert str(info.value) == "The yoma_mmqr configuration is missing [api_version]."

    def test_reads_the_environment(self, mode: str) -> None:
        env = {
            "YOMA_MMQR_MERCHANT_ID": "M1",
            "YOMA_MMQR_CLIENT_ID": "c",
            "YOMA_MMQR_CLIENT_SECRET": "s",
            "YOMA_MMQR_WEBHOOK_HASHKEY": "h",
            "YOMA_MMQR_WEBHOOK_SECRET": "w",
            "MYANMAR_PAYMENTS_HTTP_TIMEOUT": "30",
            "YOMA_MMQR_BASE_URL": "https://y.test/",
            "YOMA_MMQR_API_VERSION": "v1",
        }
        config = YomaMmqrConfig.from_env(env)
        assert config.webhook_hash_key == "h"
        assert config.webhook_secret == "w"
        assert config.timeout_seconds == 30
        assert config.base_url == "https://y.test"
        assert config.api_version == "v1"
        cls = YomaMmqr if mode == "sync" else AsyncYomaMmqr
        assert cls.from_env(env).config.merchant_id == "M1"
        with pytest.raises(ConfigurationError) as info:
            YomaMmqrConfig.from_env({**env, "YOMA_MMQR_WEBHOOK_HASHKEY": " "})
        assert info.value.key == "webhook_hashkey"
