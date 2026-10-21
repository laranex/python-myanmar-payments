from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest

from python_myanmar_payments import (
    Amount,
    ApiError,
    AsyncWavePayMiniApp,
    CallbackRequest,
    ConfigurationError,
    InvalidPaymentDataError,
    MemoryTokenCache,
    MiniAppPayment,
    PaymentFlow,
    PaymentStatus,
    SignatureVerificationError,
    WavePayMiniApp,
    WavePayMiniAppConfig,
    WavePayMiniAppItem,
    WavePayMiniAppPaymentData,
    WavePayMiniAppSigner,
)
from tests.helpers import FakeServer, gateway_config, hmac_hex, make, run

CONFIG: dict[str, Any] = gateway_config("wave_pay_mini_app", base_url="https://wave.test/")
CALLBACK_URL = "https://shop.test/wave-pay-mini-app/callback"
TOKEN = {"token_type": "Bearer", "access_token": "TOKEN", "expires_in": 300}
ENV = {
    "WAVE_PAY_MINI_APP_CLIENT_ID": "client",
    "WAVE_PAY_MINI_APP_CLIENT_SECRET": "client-secret",
    "WAVE_PAY_MINI_APP_MINI_APP_ID": "MINI456",
    "WAVE_PAY_MINI_APP_SECRET_KEY": "test-secret",
    "WAVE_PAY_MINI_APP_BASE_URL": " https://wave.test/ ",
}


def gateway(mode: str, server: FakeServer | None = None, **options: Any) -> Any:
    return make(
        mode, WavePayMiniApp, AsyncWavePayMiniApp, WavePayMiniAppConfig(**CONFIG), server, **options
    )


def data(**changes: Any) -> WavePayMiniAppPaymentData:
    values: dict[str, Any] = {
        "order_id": "ORDER_1",
        "callback_url": CALLBACK_URL,
        "items": [
            WavePayMiniAppItem("Ticket", 3000),
            WavePayMiniAppItem("Snack", Amount.kyat(2000)),
        ],
    }
    return WavePayMiniAppPaymentData(**{**values, **changes})


def callback_body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "merchantRefId": "ref-001",
        "orderId": "ORDER_1",
        "amount": 5000,
        "msisdn": "9444050894",
        "transactionId": "TX1",
        "transactionStatus": "successful",
        **changes,
    }
    body.setdefault(
        "hashValue",
        hmac_hex(
            f"{body['amount']}{body['orderId']}MINI456{body['merchantRefId']}{CALLBACK_URL}",
            "test-secret",
        ),
    )
    return body


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


class TestConfig:
    def test_reads_the_environment(self) -> None:
        config = WavePayMiniAppConfig.from_env(ENV)
        assert config.base_url == "https://wave.test"
        assert config.mini_app_id == "MINI456"
        assert config.timeout_seconds == 30
        assert repr(config) == "WavePayMiniAppConfig(mini_app_id='MINI456')"
        assert WavePayMiniAppConfig.PRODUCTION_URL == "https://api.wavemoney.io:8100"

    def test_reads_os_environ_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in ENV.items():
            monkeypatch.setenv(key, value)
        assert WavePayMiniAppConfig.from_env().client_id == "client"

    def test_requires_every_credential(self) -> None:
        with pytest.raises(ConfigurationError) as info:
            WavePayMiniAppConfig(**{**CONFIG, "mini_app_id": " "})
        assert (info.value.gateway, info.value.key) == ("wave_pay_mini_app", "mini_app_id")

    def test_gateways_read_the_environment(self, mode: str) -> None:
        cls = WavePayMiniApp if mode == "sync" else AsyncWavePayMiniApp
        cache = MemoryTokenCache()
        mini_app = cls.from_env(ENV, token_cache=cache)
        assert mini_app.config.base_url == "https://wave.test"
        assert mini_app._cache is cache

    def test_gateways_take_a_mapping(self) -> None:
        assert WavePayMiniApp(CONFIG).config.client_secret == "client-secret"


class TestAccessToken:
    def test_posts_the_client_credentials_as_a_form(self, mode: str) -> None:
        server = FakeServer(TOKEN)
        assert run(gateway(mode, server).access_token()) == "TOKEN"
        sent = server.last()
        assert sent.url == "https://wave.test/auth/v1/external-utility/token"
        assert sent.headers["content-type"] == "application/x-www-form-urlencoded"
        assert sent.headers["accept"] == "application/json"
        assert sent.form() == {
            "client_id": "client",
            "client_secret": "client-secret",
            "grant_type": "client_credentials",
        }

    def test_caches_the_token_until_forgotten(self, mode: str) -> None:
        server = FakeServer(TOKEN, {"access_token": "NEW"})
        mini_app = gateway(mode, server)
        assert run(mini_app.access_token()) == "TOKEN"
        assert run(mini_app.access_token()) == "TOKEN"
        run(mini_app.forget_token())
        assert run(mini_app.access_token()) == "NEW"
        assert len(server.requests) == 2

    def test_shares_the_cache_key_with_other_sdks(self) -> None:
        cache = MemoryTokenCache()
        run(gateway("sync", FakeServer(TOKEN), token_cache=cache).access_token())
        assert run(gateway("async", FakeServer(), token_cache=cache).access_token()) == "TOKEN"
        assert WavePayMiniApp.TOKEN_CACHE_PREFIX == "myanmar-payments.wave-pay-mini-app.token."

    def test_takes_an_async_cache(self) -> None:
        cache = AsyncCache()
        server = FakeServer(TOKEN)
        mini_app = gateway("async", server, token_cache=cache)
        assert run(mini_app.access_token()) == "TOKEN"
        assert run(mini_app.access_token()) == "TOKEN"
        assert cache.ttls == [240]
        run(mini_app.forget_token())
        assert cache.items == {}

    def test_raises_an_api_error(self, mode: str) -> None:
        server = FakeServer((401, {"error": "invalid_client", "error_description": "Bad client"}))
        with pytest.raises(ApiError) as info:
            run(gateway(mode, server).access_token())
        message = "Wave Pay Mini App token request failed: [invalid_client] Bad client"
        assert str(info.value) == message
        assert info.value.http_status == 401
        assert info.value.raw == {"error": "invalid_client", "error_description": "Bad client"}

    def test_requests_one_token_for_concurrent_async_calls(self) -> None:
        server = FakeServer(TOKEN)
        mini_app = gateway("async", server)

        async def many() -> Any:
            return await asyncio.gather(*(mini_app.access_token() for _ in range(10)))

        assert asyncio.run(many()) == ["TOKEN"] * 10
        assert len(server.requests) == 1

    def test_uses_a_token_another_caller_cached_while_it_waited(self, mode: str) -> None:
        class RacingCache(MemoryTokenCache):
            def __init__(self) -> None:
                super().__init__()
                self.reads = 0

            def get(self, key: str) -> str | None:
                self.reads += 1
                return None if self.reads == 1 else "OTHER"

        server = FakeServer()
        assert run(gateway(mode, server, token_cache=RacingCache()).access_token()) == "OTHER"
        assert server.requests == []


class TestPayment:
    def test_signs_the_make_payment_object_without_a_request(self, mode: str) -> None:
        server = FakeServer()
        payment_data = data(merchant_reference_id="ref-001")
        payment = gateway(mode, server).payment(payment_data)
        assert isinstance(payment, MiniAppPayment)
        assert payment.flow is PaymentFlow.MINI_APP
        assert payment.amount == Amount.kyat(5000)
        assert payment.items == (("Ticket", Amount.kyat(3000)), ("Snack", Amount.kyat(2000)))
        expected_hash = hmac_hex(f"5000ORDER_1MINI456ref-001{CALLBACK_URL}", "test-secret")
        assert payment.to_dict() == {
            "amount": 5000,
            "miniAppID": "MINI456",
            "merchantRefID": "ref-001",
            "orderID": "ORDER_1",
            "callBackUrl": CALLBACK_URL,
            "hashValue": expected_hash,
            "items": [{"name": "Ticket", "amount": 3000}, {"name": "Snack", "amount": 2000}],
        }
        assert json.loads(payment.to_json()) == payment.to_dict()
        assert server.requests == []

    def test_takes_a_given_amount_as_text_or_decimal(self, mode: str) -> None:
        mini_app = gateway(mode)
        assert mini_app.payment(data(amount="7000")).to_dict()["amount"] == 7000
        assert mini_app.payment(data(amount=Decimal("8000"))).amount == Amount.kyat(8000)

    def test_generates_the_merchant_reference_id(self, mode: str) -> None:
        payment_data = data()
        payment = gateway(mode).payment(payment_data)
        assert len(payment.merchant_reference_id) == 32
        assert payment_data.merchant_reference_id == payment.merchant_reference_id

    def test_leaves_invalid_data_untouched(self, mode: str) -> None:
        payment_data = data(order_id="")
        with pytest.raises(InvalidPaymentDataError) as info:
            gateway(mode).payment(payment_data)
        assert dict(info.value.errors) == {"order_id": "The order_id field is required."}
        assert payment_data.merchant_reference_id is None

    def test_rejects_items_that_are_not_a_list_and_odd_references(self) -> None:
        payment_data = data(items="Ticket", merchant_reference_id=1)
        with pytest.raises(InvalidPaymentDataError) as info:
            WavePayMiniApp.validate(payment_data)
        assert dict(info.value.errors) == {
            "items": "The items field must have at least one item.",
            "merchant_reference_id": "The merchant_reference_id field must be a string.",
            "amount": "The amount field is required.",
        }
        assert WavePayMiniApp.resolved_amount(payment_data) is None

    def test_rejects_floats(self) -> None:
        payment_data = data(items=[WavePayMiniAppItem("Ticket", 10.0)])  # type: ignore[arg-type]
        with pytest.raises(InvalidPaymentDataError) as info:
            WavePayMiniApp.validate(payment_data)
        assert list(info.value.errors) == ["items.0.amount"]


class TestSigner:
    def test_hashes_and_verifies(self) -> None:
        signer = WavePayMiniAppSigner("test-secret")
        digest = signer.hash(
            "5000", "ORDER123", "MINI456", "MERCHANT789", "https://yourdomain.com/callback"
        )
        assert digest == hmac_hex(
            "5000ORDER123MINI456MERCHANT789https://yourdomain.com/callback", "test-secret"
        )
        assert signer.verify(
            digest.upper(),
            "5000",
            "ORDER123",
            "MINI456",
            "MERCHANT789",
            "https://yourdomain.com/callback",
        )
        assert not signer.verify(
            digest, "5001", "ORDER123", "MINI456", "MERCHANT789", "https://yourdomain.com/callback"
        )
        assert isinstance(WavePayMiniApp(CONFIG).signer, WavePayMiniAppSigner)


class TestCallback:
    def test_verifies_a_callback(self, mode: str) -> None:
        request = CallbackRequest.from_json(callback_body())
        callback = gateway(mode).handle_callback(request, CALLBACK_URL)
        assert callback.is_successful()
        assert callback.order_id == "ORDER_1"
        assert callback.amount == "5000"
        assert callback.gateway_reference == "TX1"
        assert callback.raw["merchantRefId"] == "ref-001"
        assert callback.acknowledgement.status == 200
        assert callback.acknowledgement.body == ""

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (" PROCESSING ", PaymentStatus.PENDING),
            ("failed", PaymentStatus.FAILED),
            ("PAYMENT_COMPLETED", PaymentStatus.UNKNOWN),
        ],
    )
    def test_maps_statuses(self, status: str, expected: PaymentStatus) -> None:
        request = CallbackRequest.from_json(callback_body(transactionStatus=status))
        callback = WavePayMiniApp(CONFIG).handle_callback(request, CALLBACK_URL)
        assert callback.status is expected
        assert callback.gateway_status == status.strip()

    def test_rejects_a_wrong_callback_url(self) -> None:
        request = CallbackRequest.from_json(callback_body())
        with pytest.raises(SignatureVerificationError) as info:
            WavePayMiniApp(CONFIG).handle_callback(request, "https://shop.test/other")
        assert str(info.value) == "Wave Pay Mini App callback hash verification failed."

    def test_rejects_a_malformed_callback(self) -> None:
        request = CallbackRequest.from_json(callback_body(amount="10.5"))
        with pytest.raises(SignatureVerificationError) as info:
            WavePayMiniApp(CONFIG).handle_callback(request, CALLBACK_URL)
        assert str(info.value) == "Wave Pay Mini App callback is malformed."
        assert info.value.raw["amount"] == "10.5"
