from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs

import pytest

from python_myanmar_payments import (
    Amount,
    ApiError,
    AsyncWaveMoney,
    CallbackRequest,
    ConfigurationError,
    InvalidPaymentDataError,
    PaymentStatus,
    SignatureVerificationError,
    WaveMoney,
    WaveMoneyConfig,
    WaveMoneyItem,
    WaveMoneyPaymentData,
)
from tests.helpers import FakeServer, fixture, hmac_hex, make, run

CONFIG: dict[str, Any] = {
    "merchant_id": "testmerchantID",
    "secret_key": "test-secret",
    "merchant_name": "Shop",
    "base_url": "https://wave.test",
}

SUCCESS = {"message": "success", "transaction_id": "TX 1"}


def gateway(mode: str, server: FakeServer | None = None) -> Any:
    return make(mode, WaveMoney, AsyncWaveMoney, WaveMoneyConfig(**CONFIG), server)


def data(**changes: Any) -> WaveMoneyPaymentData:
    values: dict[str, Any] = {
        "order_id": "100",
        "callback_url": "https://shop.test/wave/callback",
        "return_url": "https://shop.test/done",
        "description": "Order 100",
        "items": [WaveMoneyItem("Shoes", 600), WaveMoneyItem("Socks", Amount.kyat(400))],
    }
    return WaveMoneyPaymentData(**{**values, **changes})


def form(server: FakeServer) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(server.last().body, True).items()}


class TestWaveMoney:
    def test_posts_a_hashed_form_and_returns_the_authenticate_url(self, mode: str) -> None:
        server = FakeServer(SUCCESS)
        payment_data = data(merchant_reference_id="ref-001")
        payment = run(gateway(mode, server).initiate(payment_data))

        sent = form(server)
        assert server.last().url == "https://wave.test/payment"
        assert server.last().headers["content-type"] == "application/x-www-form-urlencoded"
        assert sent == {
            "time_to_live_in_seconds": "300",
            "merchant_id": "testmerchantID",
            "order_id": "100",
            "merchant_reference_id": "ref-001",
            "frontend_result_url": "https://shop.test/done",
            "backend_result_url": "https://shop.test/wave/callback",
            "amount": "1000",
            "payment_description": "Order 100",
            "merchant_name": "Shop",
            "items": '[{"name":"Shoes","amount":600},{"name":"Socks","amount":400}]',
            "hash": hmac_hex(
                "300testmerchantID1001000https://shop.test/wave/callbackref-001", "test-secret"
            ),
        }
        assert payment.flow == "redirect"
        assert (
            payment.url == "https://preprodpayments.wavemoney.io/authenticate?transaction_id=TX+1"
        )
        assert payment.gateway_reference == "TX 1"
        assert payment.raw == SUCCESS

    def test_fills_a_random_merchant_reference_id(self, mode: str) -> None:
        payment_data = data()
        run(gateway(mode, FakeServer(SUCCESS)).initiate(payment_data))
        assert payment_data.merchant_reference_id is not None
        assert len(payment_data.merchant_reference_id) == 32

    def test_charges_an_explicit_amount_and_escapes_item_names(self, mode: str) -> None:
        server = FakeServer(SUCCESS)
        items = [WaveMoneyItem('Tea "green" ü', Decimal(1000))]
        run(gateway(mode, server).initiate(data(items=items, amount="1000")))
        sent = form(server)
        assert sent["amount"] == "1000"
        assert json.loads(sent["items"]) == [{"name": 'Tea "green" ü', "amount": 1000}]

    @pytest.mark.parametrize(
        ("status", "body", "code", "message"),
        [
            (
                422,
                {"errors": {"order_id": ["is required"], "amount": "too small"}},
                "VALIDATION_ERROR",
                "amount: too small; order_id: is required",
            ),
            (200, {"message": "failed"}, "failed", "failed"),
            (200, {"message": "success"}, "success", "success"),
            (500, "oops", None, "unexpected response"),
        ],
    )
    def test_raises_wave_errors(
        self, mode: str, status: int, body: Any, code: str | None, message: str
    ) -> None:
        with pytest.raises(ApiError) as info:
            run(gateway(mode, FakeServer((status, body))).initiate(data()))
        assert info.value.gateway_code == code
        assert info.value.gateway_message == message
        assert info.value.http_status == status
        assert str(info.value) == (
            f"Wave Money payment request failed with HTTP {status}: {message}"
        )

    def test_leaves_invalid_data_untouched(self, mode: str) -> None:
        payment_data = data(order_id="")
        with pytest.raises(InvalidPaymentDataError):
            run(gateway(mode).initiate(payment_data))
        assert payment_data.merchant_reference_id is None

    def test_resolves_the_amount(self) -> None:
        assert WaveMoney.resolved_amount(data()) == Amount.kyat(1000)
        assert WaveMoney.resolved_amount(data(amount=5)) == Amount.kyat(5)
        assert WaveMoney.resolved_amount(data(items=[])) is None
        assert WaveMoney.resolved_amount(data(items="x")) is None
        assert WaveMoney.resolved_amount(data(items=[WaveMoneyItem("a", "1.5")])) is None
        no_amount = WaveMoneyItem("a", None)  # type: ignore[arg-type]
        assert WaveMoney.resolved_amount(data(items=[no_amount])) is None

    @pytest.mark.parametrize(
        ("change", "field"),
        [
            ({"order_id": ""}, "order_id"),
            ({"callback_url": "ftp://shop.test"}, "callback_url"),
            ({"return_url": ""}, "return_url"),
            ({"return_url": "not a url"}, "return_url"),
            ({"description": " "}, "description"),
            ({"items": []}, "items"),
            ({"items": [WaveMoneyItem("", 1000)]}, "items.0.name"),
            ({"items": [WaveMoneyItem("a", "10.50")]}, "items.0.amount"),
            ({"items": [WaveMoneyItem("a", 10.5)]}, "items.0.amount"),  # type: ignore[arg-type]
            ({"items": ["junk"]}, "items.0.amount"),
            ({"amount": "10.50"}, "amount"),
            ({"amount": 0}, "amount"),
            ({"items": [], "amount": None}, "amount"),
            ({"merchant_reference_id": 5}, "merchant_reference_id"),
        ],
    )
    def test_validates_against_wave_rules(self, change: Any, field: str) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            WaveMoney.validate(data(**change))
        assert info.value.errors[field]

    def test_does_not_report_the_total_when_an_item_is_invalid(self) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            WaveMoney.validate(data(items=[WaveMoneyItem("a", "1.5")]))
        assert "amount" not in info.value.errors
        assert "decimal" in info.value.errors["items.0.amount"]

    def test_verifies_the_shared_callback_vector(self, mode: str) -> None:
        vector = fixture("wave_money/callback.json")
        payload = {**vector["payload"], "hashValue": hmac_hex(vector["hash_string"], "test-secret")}
        callback = gateway(mode).handle_callback(CallbackRequest.from_json(payload))
        assert callback.is_successful()
        assert callback.order_id == "100"
        assert callback.gateway_reference == "360"
        assert callback.amount == "1000"
        assert callback.raw["timeToLiveSeconds"] == "300"
        assert callback.acknowledgement.body == ""

    def test_verifies_json_numbers_with_their_exact_text(self) -> None:
        vector = fixture("wave_money/callback.json")
        hash_string = vector["hash_string"].replace("1001000https", "1001000.00https")
        body = json.dumps({**vector["payload"], "amount": "AMOUNT"}).replace('"AMOUNT"', "1000.00")
        body = body[:-1] + f', "hashValue": "{hmac_hex(hash_string, "test-secret").upper()}"}}'
        callback = gateway("sync").handle_callback(CallbackRequest(body=body))
        assert callback.amount == "1000.00"

    def test_falls_back_to_the_merchant_reference_id(self) -> None:
        vector = fixture("wave_money/callback.json")
        hash_string = vector["hash_string"].replace("testmerchantID100", "testmerchantIDnull")
        payload = {
            **vector["payload"],
            "orderId": None,
            "hashValue": hmac_hex(hash_string, "test-secret"),
        }
        callback = gateway("sync").handle_callback(CallbackRequest.from_json(payload))
        assert callback.order_id == "ref-001"

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("INSUFFICIENT_BALANCE", PaymentStatus.PENDING),
            ("ACCOUNT_LOCKED", PaymentStatus.FAILED),
            ("BILL_COLLECTION_FAILED", PaymentStatus.FAILED),
            ("PAYMENT_REQUEST_CANCELLED", PaymentStatus.CANCELED),
            ("TRANSACTION_TIMED_OUT", PaymentStatus.EXPIRED),
            ("SCHEDULER_TRANSACTION_TIMED_OUT", PaymentStatus.EXPIRED),
            ("NEW", PaymentStatus.UNKNOWN),
        ],
    )
    def test_maps_callback_statuses(self, status: str, expected: PaymentStatus) -> None:
        vector = fixture("wave_money/callback.json")
        hash_string = status + vector["hash_string"].removeprefix("PAYMENT_CONFIRMED")
        payload = {
            **vector["payload"],
            "status": status,
            "hashValue": hmac_hex(hash_string, "test-secret"),
        }
        assert (
            gateway("sync").handle_callback(CallbackRequest.from_json(payload)).status is expected
        )

    def test_rejects_a_tampered_or_unsigned_callback(self) -> None:
        vector = fixture("wave_money/callback.json")
        payload = {
            **vector["payload"],
            "amount": "1",
            "hashValue": hmac_hex(vector["hash_string"], "test-secret"),
        }
        with pytest.raises(SignatureVerificationError) as info:
            gateway("sync").handle_callback(CallbackRequest.from_json(payload))
        assert info.value.raw["amount"] == "1"
        with pytest.raises(SignatureVerificationError, match="Wave Money callback hash"):
            gateway("sync").handle_callback(CallbackRequest.from_json(vector["payload"]))


class TestWaveMoneyConfig:
    def test_uses_the_documented_hosts(self) -> None:
        sandbox = WaveMoneyConfig(merchant_id="m", secret_key="s", merchant_name="n")
        assert sandbox.base_url == "https://preprodpayments.wavemoney.io:8107"
        assert sandbox.authenticate_url == "https://preprodpayments.wavemoney.io"
        assert sandbox.time_to_live_seconds == 300
        production = WaveMoneyConfig(
            merchant_id="m", secret_key="s", merchant_name="n", sandbox=False
        )
        assert production.base_url == "https://payments.wavemoney.io"
        assert production.authenticate_url == "https://payments.wavemoney.io"
        assert repr(production) == "WaveMoneyConfig(merchant_id='m', sandbox=False)"

    @pytest.mark.parametrize(("ttl", "expected"), [(60, 60), (0, 300), (None, 300), (True, 300)])
    def test_keeps_only_a_positive_time_to_live(self, ttl: Any, expected: int) -> None:
        config = WaveMoneyConfig(
            merchant_id="m", secret_key="s", merchant_name="n", time_to_live_seconds=ttl
        )
        assert config.time_to_live_seconds == expected

    def test_reads_the_environment(self, mode: str) -> None:
        env = {
            "WAVE_MONEY_MERCHANT_ID": "m",
            "WAVE_MONEY_SECRET_KEY": "s",
            "APP_NAME": "My Shop",
            "WAVE_MONEY_TIME_TO_LIVE_IN_SECONDS": " 600 ",
            "WAVE_MONEY_SANDBOX": "OFF",
            "WAVE_MONEY_BASE_URL": "https://api.wave.test/",
            "WAVE_MONEY_AUTHENTICATE_URL": "https://pay.wave.test/",
        }
        config = WaveMoneyConfig.from_env(env)
        assert config.merchant_name == "My Shop"
        assert config.time_to_live_seconds == 600
        assert config.sandbox is False
        assert config.base_url == "https://api.wave.test"
        assert config.authenticate_url == "https://pay.wave.test"
        env_with_name = {**env, "WAVE_MONEY_MERCHANT_NAME": "Wave Shop"}
        cls = WaveMoney if mode == "sync" else AsyncWaveMoney
        assert cls.from_env(env_with_name).config.merchant_name == "Wave Shop"
        bad_ttl = {**env, "WAVE_MONEY_TIME_TO_LIVE_IN_SECONDS": "10m"}
        assert WaveMoneyConfig.from_env(bad_ttl).time_to_live_seconds == 300
        with pytest.raises(ConfigurationError) as info:
            WaveMoneyConfig.from_env({"WAVE_MONEY_MERCHANT_ID": "m", "WAVE_MONEY_SECRET_KEY": "s"})
        assert info.value.key == "merchant_name"
