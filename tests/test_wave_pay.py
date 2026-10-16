from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from python_myanmar_payments import (
    Amount,
    ApiError,
    AsyncWavePay,
    CallbackRequest,
    ConfigurationError,
    InvalidPaymentDataError,
    PaymentStatus,
    SignatureVerificationError,
    WavePay,
    WavePayConfig,
    WavePayItem,
    WavePayPaymentData,
)
from tests.helpers import FakeServer, fixture, gateway_config, hmac_hex, make, run

CONFIG: dict[str, Any] = gateway_config(
    "wave_pay",
    timeout_seconds=30,
    base_url="https://wave.test",
    base_redirect_url="https://pay.wave.test/",
)

SUCCESS = {"message": "success", "transaction_id": "TX 1"}


def gateway(mode: str, server: FakeServer | None = None) -> Any:
    return make(mode, WavePay, AsyncWavePay, WavePayConfig(**CONFIG), server)


def data(**changes: Any) -> WavePayPaymentData:
    values: dict[str, Any] = {
        "order_id": "100",
        "callback_url": "https://shop.test/wave/callback",
        "return_url": "https://shop.test/done",
        "description": "Order 100",
        "items": [WavePayItem("Shoes", 600), WavePayItem("Socks", Amount.kyat(400))],
    }
    return WavePayPaymentData(**{**values, **changes})


def sent_json(server: FakeServer) -> dict[str, Any]:
    return server.last().json()


class TestWavePay:
    def test_posts_to_the_base_url_and_redirects_to_the_base_redirect_url(self, mode: str) -> None:
        server = FakeServer(SUCCESS)
        payment_data = data(merchant_reference_id="ref-001")
        payment = run(gateway(mode, server).initiate(payment_data))

        sent = sent_json(server)
        assert server.last().url == "https://wave.test/payment"
        assert server.last().headers["content-type"] == "application/json"
        assert server.last().headers["accept"] == "application/json"
        assert sent == {
            "merchant_id": "testmerchantID",
            "order_id": "100",
            "merchant_reference_id": "ref-001",
            "frontend_result_url": "https://shop.test/done",
            "backend_result_url": "https://shop.test/wave/callback",
            "amount": 1000,
            "time_to_live_in_seconds": 300,
            "payment_description": "Order 100",
            "currency": "MMK",
            "hash": hmac_hex(
                "300testmerchantID1001000https://shop.test/wave/callbackref-001", "test-secret"
            ),
            "merchant_name": "Shop",
            "items": '[{"name":"Shoes","amount":600},{"name":"Socks","amount":400}]',
        }
        assert payment.flow == "redirect"
        assert payment.url == "https://pay.wave.test/authenticate?transaction_id=TX+1"
        assert payment.gateway_reference == "TX 1"
        assert payment.raw == SUCCESS

    def test_defaults_to_the_configured_merchant_name_and_time_to_live(self, mode: str) -> None:
        server = FakeServer(SUCCESS)
        run(gateway(mode, server).initiate(data(merchant_reference_id="ref-001")))
        sent = sent_json(server)
        assert sent["merchant_name"] == "Shop"
        assert sent["time_to_live_in_seconds"] == 300

    def test_overrides_the_merchant_name_and_time_to_live_per_payment(self, mode: str) -> None:
        server = FakeServer(SUCCESS)
        payment_data = data(
            merchant_reference_id="ref-001", merchant_name="Tikkat", time_to_live_seconds=600
        )
        run(gateway(mode, server).initiate(payment_data))
        sent = sent_json(server)
        assert sent["merchant_name"] == "Tikkat"
        assert sent["time_to_live_in_seconds"] == 600
        assert sent["hash"] == hmac_hex(
            "600testmerchantID1001000https://shop.test/wave/callbackref-001", "test-secret"
        )

    def test_reports_override_messages(self) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            WavePay.validate(data(merchant_name=" ", time_to_live_seconds=0))
        assert dict(info.value.errors) == {
            "merchant_name": "The merchant_name field is required.",
            "time_to_live_seconds": "The time_to_live_seconds field must be greater than 0.",
        }
        for ttl in (1.5, "300", True):
            with pytest.raises(InvalidPaymentDataError) as info:
                WavePay.validate(data(time_to_live_seconds=ttl))
            assert dict(info.value.errors) == {
                "time_to_live_seconds": "The time_to_live_seconds field must be an integer."
            }

    def test_sends_only_the_additional_fields_that_are_set(self, mode: str) -> None:
        server = FakeServer(SUCCESS)
        payment_data = data(
            additional_field_3="Info 3", additional_field_4="", additional_field_5=None
        )
        run(gateway(mode, server).initiate(payment_data))
        sent = sent_json(server)
        assert sent["additional_field_3"] == "Info 3"
        assert "additional_field_4" not in sent
        assert "additional_field_5" not in sent

    def test_reports_field_limit_messages(self) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            WavePay.validate(
                data(
                    order_id="O" * 101,
                    merchant_reference_id="r" * 101,
                    callback_url="https://shop.test/" + "a" * 238,
                    return_url="https://shop.test/" + "a" * 238,
                    description="ဒ" * 86,
                    additional_field_3="x" * 51,
                    additional_field_4="x" * 51,
                    additional_field_5="x" * 51,
                )
            )
        assert dict(info.value.errors) == {
            "order_id": "The order_id field must not be greater than 100 characters.",
            "merchant_reference_id": (
                "The merchant_reference_id field must not be greater than 100 characters."
            ),
            "callback_url": "The callback_url field must not be greater than 255 characters.",
            "return_url": "The return_url field must not be greater than 255 characters.",
            "description": "The description field must not be greater than 255 characters.",
            "additional_field_3": (
                "The additional_field_3 field must not be greater than 50 characters."
            ),
            "additional_field_4": (
                "The additional_field_4 field must not be greater than 50 characters."
            ),
            "additional_field_5": (
                "The additional_field_5 field must not be greater than 50 characters."
            ),
        }

    def test_fills_a_random_merchant_reference_id(self, mode: str) -> None:
        payment_data = data()
        run(gateway(mode, FakeServer(SUCCESS)).initiate(payment_data))
        assert payment_data.merchant_reference_id is not None
        assert len(payment_data.merchant_reference_id) == 32

    def test_charges_an_explicit_amount_and_escapes_item_names(self, mode: str) -> None:
        server = FakeServer(SUCCESS)
        items = [WavePayItem('Tea "green" ü', Decimal(1000))]
        run(gateway(mode, server).initiate(data(items=items, amount="1000")))
        sent = sent_json(server)
        assert sent["amount"] == 1000
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
        assert str(info.value) == (f"Wave Pay payment request failed with HTTP {status}: {message}")

    def test_leaves_invalid_data_untouched(self, mode: str) -> None:
        payment_data = data(order_id="")
        with pytest.raises(InvalidPaymentDataError):
            run(gateway(mode).initiate(payment_data))
        assert payment_data.merchant_reference_id is None

    def test_resolves_the_amount(self) -> None:
        assert WavePay.resolved_amount(data()) == Amount.kyat(1000)
        assert WavePay.resolved_amount(data(amount=5)) == Amount.kyat(5)
        assert WavePay.resolved_amount(data(items=[])) is None
        assert WavePay.resolved_amount(data(items="x")) is None
        assert WavePay.resolved_amount(data(items=[WavePayItem("a", "1.5")])) is None
        no_amount = WavePayItem("a", None)  # type: ignore[arg-type]
        assert WavePay.resolved_amount(data(items=[no_amount])) is None

    @pytest.mark.parametrize(
        ("change", "field"),
        [
            ({"order_id": ""}, "order_id"),
            ({"callback_url": "ftp://shop.test"}, "callback_url"),
            ({"return_url": ""}, "return_url"),
            ({"return_url": "not a url"}, "return_url"),
            ({"description": " "}, "description"),
            ({"items": []}, "items"),
            ({"items": [WavePayItem("", 1000)]}, "items.0.name"),
            ({"items": [WavePayItem("a", "10.50")]}, "items.0.amount"),
            ({"items": [WavePayItem("a", 10.5)]}, "items.0.amount"),  # type: ignore[arg-type]
            ({"items": ["junk"]}, "items.0.amount"),
            ({"amount": "10.50"}, "amount"),
            ({"amount": 0}, "amount"),
            ({"items": [], "amount": None}, "amount"),
            ({"order_id": "ORDER 1"}, "order_id"),
            ({"order_id": "ORDER:1"}, "order_id"),
            ({"merchant_reference_id": 5}, "merchant_reference_id"),
            ({"merchant_reference_id": "ref/1"}, "merchant_reference_id"),
            ({"merchant_name": ""}, "merchant_name"),
            ({"merchant_name": " "}, "merchant_name"),
            ({"merchant_name": 5}, "merchant_name"),
            ({"time_to_live_seconds": 0}, "time_to_live_seconds"),
            ({"time_to_live_seconds": -5}, "time_to_live_seconds"),
            ({"time_to_live_seconds": True}, "time_to_live_seconds"),
            ({"time_to_live_seconds": 600.0}, "time_to_live_seconds"),
            ({"time_to_live_seconds": "600"}, "time_to_live_seconds"),
            ({"additional_field_3": 5}, "additional_field_3"),
            ({"additional_field_4": "x" * 51}, "additional_field_4"),
            ({"additional_field_5": "ဒ" * 17}, "additional_field_5"),
        ],
    )
    def test_validates_against_wave_rules(self, change: Any, field: str) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            WavePay.validate(data(**change))
        assert info.value.errors[field]

    def test_does_not_report_the_total_when_an_item_is_invalid(self) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            WavePay.validate(data(items=[WavePayItem("a", "1.5")]))
        assert "amount" not in info.value.errors
        assert "decimal" in info.value.errors["items.0.amount"]

    def test_verifies_the_shared_callback_vector(self, mode: str) -> None:
        vector = fixture("wave_pay/callback.json")
        payload = {**vector["payload"], "hashValue": hmac_hex(vector["hash_string"], "test-secret")}
        callback = gateway(mode).handle_callback(CallbackRequest.from_json(payload))
        assert callback.is_successful()
        assert callback.order_id == "100"
        assert callback.gateway_reference == "360"
        assert callback.amount == "1000"
        assert callback.raw["timeToLiveSeconds"] == "300"
        assert callback.acknowledgement.body == ""

    def test_verifies_json_numbers_with_their_exact_text(self) -> None:
        vector = fixture("wave_pay/callback.json")
        hash_string = vector["hash_string"].replace("1001000https", "1001000.00https")
        body = json.dumps({**vector["payload"], "amount": "AMOUNT"}).replace('"AMOUNT"', "1000.00")
        body = body[:-1] + f', "hashValue": "{hmac_hex(hash_string, "test-secret").upper()}"}}'
        callback = gateway("sync").handle_callback(CallbackRequest(body=body))
        assert callback.amount == "1000.00"

    def test_falls_back_to_the_merchant_reference_id(self) -> None:
        vector = fixture("wave_pay/callback.json")
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
            ("INSUFFICIENT_BALANCE", PaymentStatus.FAILED),
            ("ACCOUNT_LOCKED", PaymentStatus.FAILED),
            ("BILL_COLLECTION_FAILED", PaymentStatus.FAILED),
            ("PAYMENT_REQUEST_CANCELLED", PaymentStatus.CANCELED),
            ("TRANSACTION_TIMED_OUT", PaymentStatus.EXPIRED),
            ("SCHEDULER_TRANSACTION_TIMED_OUT", PaymentStatus.EXPIRED),
            ("NEW", PaymentStatus.UNKNOWN),
        ],
    )
    def test_maps_callback_statuses(self, status: str, expected: PaymentStatus) -> None:
        vector = fixture("wave_pay/callback.json")
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
        vector = fixture("wave_pay/callback.json")
        payload = {
            **vector["payload"],
            "amount": "1",
            "hashValue": hmac_hex(vector["hash_string"], "test-secret"),
        }
        with pytest.raises(SignatureVerificationError) as info:
            gateway("sync").handle_callback(CallbackRequest.from_json(payload))
        assert info.value.raw["amount"] == "1"
        with pytest.raises(SignatureVerificationError, match="Wave Pay callback hash"):
            gateway("sync").handle_callback(CallbackRequest.from_json(vector["payload"]))

    @pytest.mark.parametrize(
        "changes",
        [
            {"amount": None},
            {"amount": ""},
            {"amount": "-1000"},
            {"amount": "1e3"},
            {"amount": "1000."},
            {"amount": "10,00"},
            {"orderId": "100 1"},
            {"orderId": "", "merchantReferenceId": ""},
            {"orderId": None, "merchantReferenceId": None},
            {"merchantReferenceId": "ref:001"},
            {"orderId": None, "merchantReferenceId": "ref 001"},
        ],
    )
    def test_rejects_hashed_callbacks_with_malformed_values(
        self, mode: str, changes: dict[str, Any]
    ) -> None:
        names = [
            "status",
            "timeToLiveSeconds",
            "merchantId",
            "orderId",
            "amount",
            "backendResultUrl",
            "merchantReferenceId",
            "initiatorMsisdn",
            "transactionId",
            "paymentRequestId",
            "requestTime",
        ]
        payload = {**fixture("wave_pay/callback.json")["payload"], **changes}
        text = "".join(
            "null" if payload.get(name) is None else str(payload[name]) for name in names
        )
        payload["hashValue"] = hmac_hex(text, "test-secret")
        with pytest.raises(SignatureVerificationError, match="Wave Pay callback is malformed"):
            gateway(mode).handle_callback(CallbackRequest.from_json(payload))

    def test_accepts_dashes_and_underscores_in_the_ids(self) -> None:
        vector = fixture("wave_pay/callback.json")
        hash_string = vector["hash_string"].replace("100", "ORDER_1-a", 1)
        payload = {
            **vector["payload"],
            "orderId": "ORDER_1-a",
            "hashValue": hmac_hex(hash_string, "test-secret"),
        }
        callback = gateway("sync").handle_callback(CallbackRequest.from_json(payload))
        assert callback.order_id == "ORDER_1-a"


ENV = {
    "WAVE_PAY_MERCHANT_ID": "m",
    "WAVE_PAY_SECRET_KEY": "s",
    "WAVE_PAY_MERCHANT_NAME": "My Shop",
    "WAVE_PAY_BASE_URL": "https://api.wave.test/",
    "WAVE_PAY_BASE_REDIRECT_URL": "https://pay.wave.test/",
    "WAVE_PAY_TIME_TO_LIVE_IN_SECONDS": " 600 ",
    "MYANMAR_PAYMENTS_HTTP_TIMEOUT": "20",
}


class TestWavePayConfig:
    def test_uses_the_configured_hosts(self) -> None:
        config = WavePayConfig(
            **{
                **CONFIG,
                "base_url": WavePayConfig.PRODUCTION_URL,
                "base_redirect_url": WavePayConfig.PRODUCTION_URL,
            }
        )
        assert config.base_url == "https://payments.wavemoney.io"
        assert config.base_redirect_url == "https://payments.wavemoney.io"
        assert config.time_to_live_seconds == 300
        assert config.timeout_seconds == 30
        assert repr(config) == "WavePayConfig(merchant_id='testmerchantID')"
        assert WavePayConfig(**CONFIG).base_redirect_url == "https://pay.wave.test"

    @pytest.mark.parametrize(("ttl", "expected"), [(60, 60), ("600", 600), (" +5 ", 5)])
    def test_reads_a_whole_number_time_to_live(self, ttl: Any, expected: int) -> None:
        config = WavePayConfig(**{**CONFIG, "time_to_live_seconds": ttl})
        assert config.time_to_live_seconds == expected

    @pytest.mark.parametrize("ttl", [0, -1, True, 1.5, "10m"])
    def test_rejects_a_time_to_live_that_is_not_a_whole_number(self, ttl: Any) -> None:
        with pytest.raises(ConfigurationError) as info:
            WavePayConfig(**{**CONFIG, "time_to_live_seconds": ttl})
        assert str(info.value) == (
            "The wave_pay configuration [time_to_live_in_seconds] must be a whole number"
            " greater than 0."
        )

    @pytest.mark.parametrize(
        ("key", "name"),
        [
            ("base_url", "base_url"),
            ("base_redirect_url", "base_redirect_url"),
            ("time_to_live_seconds", "time_to_live_in_seconds"),
        ],
    )
    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_requires_the_urls_and_the_time_to_live(self, key: str, name: str, blank: Any) -> None:
        with pytest.raises(ConfigurationError) as info:
            WavePayConfig(**{**CONFIG, key: blank})
        assert info.value.gateway == "wave_pay"
        assert info.value.key == name
        assert str(info.value) == f"The wave_pay configuration is missing [{name}]."
        with pytest.raises(ConfigurationError, match=rf"missing \[{name}\]"):
            WavePayConfig(**{k: v for k, v in CONFIG.items() if k != key})

    @pytest.mark.parametrize(
        ("variable", "key"),
        [
            ("WAVE_PAY_BASE_URL", "base_url"),
            ("WAVE_PAY_BASE_REDIRECT_URL", "base_redirect_url"),
            ("WAVE_PAY_TIME_TO_LIVE_IN_SECONDS", "time_to_live_in_seconds"),
        ],
    )
    def test_requires_each_setting_in_the_environment(self, variable: str, key: str) -> None:
        for env in ({k: v for k, v in ENV.items() if k != variable}, {**ENV, variable: "  "}):
            with pytest.raises(ConfigurationError) as info:
                WavePayConfig.from_env(env)
            assert str(info.value) == f"The wave_pay configuration is missing [{key}]."

    def test_reads_the_environment(self, mode: str) -> None:
        env = ENV
        config = WavePayConfig.from_env(env)
        assert config.merchant_name == "My Shop"
        assert config.time_to_live_seconds == 600
        assert config.timeout_seconds == 20
        assert config.base_url == "https://api.wave.test"
        assert config.base_redirect_url == "https://pay.wave.test"
        env_with_name = {**env, "WAVE_PAY_MERCHANT_NAME": "Wave Shop"}
        cls = WavePay if mode == "sync" else AsyncWavePay
        assert cls.from_env(env_with_name).config.merchant_name == "Wave Shop"
        with pytest.raises(ConfigurationError) as info:
            WavePayConfig.from_env({**env, "WAVE_PAY_MERCHANT_NAME": "", "APP_NAME": "Shop"})
        assert info.value.key == "merchant_name"
