from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import quote, urlencode

import pytest

from python_myanmar_payments import (
    Amount,
    ApiError,
    AsyncAyaPay,
    AyaPay,
    AyaPayConfig,
    AyaPayMethod,
    AyaPayPaymentData,
    AyaPayService,
    CallbackRequest,
    ConfigurationError,
    InvalidPaymentDataError,
    PaymentStatus,
    SignatureVerificationError,
)
from tests.conftest import Clock
from tests.helpers import NOW, FakeServer, fixture, hmac_hex, make, run

SECRET = "test-secret"
CONFIG: dict[str, Any] = {
    "app_key": "app-key",
    "app_secret": SECRET,
    "base_url": "https://aya.test",
}


def gateway(mode: str, server: FakeServer | None = None) -> Any:
    return make(mode, AyaPay, AsyncAyaPay, AyaPayConfig(**CONFIG), server)


def data(**changes: Any) -> AyaPayPaymentData:
    values: dict[str, Any] = {
        "order_id": "ORDER123",
        "amount": Amount.kyat(1000),
        "channel": "kbz_pay",
        "method": AyaPayMethod.QR,
    }
    return AyaPayPaymentData(**{**values, **changes})


def encode(payload: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(payload).encode()).decode()


def signed(payload: dict[str, Any] | None = None) -> dict[str, str]:
    """The shared vector's payload and checksum, or another payload signed like AYA."""
    vector = fixture("aya_pay/callback_payload.json")
    if payload is None:
        return {
            "payload": encode(vector["payload"]),
            "checkSum": hmac_hex(vector["checksum_string"], SECRET),
        }
    names = [
        "merchOrderId",
        "tranId",
        "amount",
        "currenyCode",
        "statusCode",
        "tranRef",
        "description",
    ]
    parts = [str(payload[name]) for name in names if name in payload]
    return {"payload": encode(payload), "checkSum": hmac_hex(":".join(parts), SECRET)}


class TestAyaPay:
    def test_signs_the_checkout_form(self, mode: str, clock: Clock) -> None:
        payment = gateway(mode).initiate(
            data(
                return_url="https://shop.test/aya/return",
                description="Order 123",
                user_refs=["cart-9", "x"],
            )
        )
        values = payment.values()
        assert payment.flow == "form"
        assert payment.action == "https://aya.test/v1/payment/request"
        assert payment.enctype == "multipart/form-data"
        assert [field.name for field in payment.fields] == [
            "merchOrderId",
            "amount",
            "appKey",
            "timestamp",
            "userRef1",
            "userRef2",
            "userRef3",
            "userRef4",
            "userRef5",
            "description",
            "currencyCode",
            "channel",
            "method",
            "overrideFrontendRedirectUrl",
            "checkSum",
        ]
        assert values["timestamp"] == str(NOW)
        assert values["currencyCode"] == "104"
        assert values["method"] == "QR"
        expected = ":".join(
            [
                "ORDER123",
                "1000",
                "app-key",
                str(NOW),
                "cart-9",
                "x",
                "",
                "",
                "",
                "Order 123",
                "104",
                "kbz_pay",
                "QR",
                "https://shop.test/aya/return",
            ]
        )
        assert values["checkSum"] == hmac_hex(expected, SECRET)

    def test_defaults_the_optional_fields_to_empty_text(self) -> None:
        values = gateway("sync").initiate(data(method="WEB")).values()
        assert values["description"] == ""
        assert values["overrideFrontendRedirectUrl"] == ""
        assert values["userRef1"] == ""
        assert values["method"] == "WEB"

    def test_lists_services(self, mode: str, clock: Clock) -> None:
        server = FakeServer(
            {
                "status": "00",
                "data": [
                    {
                        "name": "AYA Pay",
                        "key": "aya_pay",
                        "image_url": "https://img",
                        "methods": ["QR", "NOTI", "BANK"],
                    },
                    {"key": "visa", "methods": "WEB"},
                    {"name": "No key"},
                    "junk",
                ],
            }
        )
        services = run(gateway(mode, server).services())
        sent = server.last().json()
        assert server.last().url == "https://aya.test/v1/payment/services"
        assert sent == {
            "appKey": "app-key",
            "timestamp": NOW,
            "checkSum": hmac_hex(f"app-key:{SECRET}:{NOW}", SECRET),
        }
        assert services == [
            AyaPayService(
                name="AYA Pay",
                key="aya_pay",
                image_url="https://img",
                methods=(AyaPayMethod.QR, AyaPayMethod.NOTI),
                unknown_methods=("BANK",),
            ),
            AyaPayService(name="visa", key="visa"),
        ]
        assert services[0].supports(AyaPayMethod.QR)
        assert services[0].supports("NOTI")
        assert not services[0].supports(AyaPayMethod.WEB)

    def test_lists_no_services_without_data(self, mode: str) -> None:
        assert run(gateway(mode, FakeServer({"status": "00"})).services()) == []

    @pytest.mark.parametrize(
        ("reply", "message", "code"),
        [
            (
                (200, {"status": "01", "message": "Invalid"}),
                "AYA Pay services failed: [01] Invalid",
                "01",
            ),
            ((200, {"status": "09"}), "AYA Pay services failed: [09]", "09"),
            ((503, "down"), "AYA Pay services failed with HTTP 503.", None),
        ],
    )
    def test_raises_gateway_errors(
        self, mode: str, reply: Any, message: str, code: str | None
    ) -> None:
        with pytest.raises(ApiError) as info:
            run(gateway(mode, FakeServer(reply)).services())
        assert str(info.value) == message
        assert info.value.gateway_code == code

    def test_checks_the_status_with_a_verified_enquiry(self, mode: str, clock: Clock) -> None:
        server = FakeServer({"status": "00", "data": signed()})
        result = run(gateway(mode, server).status("ORDER123456"))
        assert server.last().url == "https://aya.test/v1/payment/enquiry"
        assert server.last().json() == {
            "merchOrderId": "ORDER123456",
            "appKey": "app-key",
            "timestamp": NOW,
            "checkSum": hmac_hex(f"ORDER123456:{NOW}:app-key", SECRET),
        }
        assert result.order_id == "ORD123456"
        assert result.status is PaymentStatus.SUCCESSFUL
        assert result.gateway_status == "00"
        assert result.gateway_reference == "TRN0001"
        assert result.amount == "1000"
        assert result.raw["paymentCardNumber"] is None

    def test_keeps_the_queried_order_id_when_the_payload_has_none(self, mode: str) -> None:
        server = FakeServer({"status": "00", "data": signed({"statusCode": "01"})})
        result = run(gateway(mode, server).status("ORDER999"))
        assert result.order_id == "ORDER999"
        assert result.status is PaymentStatus.PENDING

    def test_rejects_an_unsigned_enquiry_response(self, mode: str) -> None:
        with pytest.raises(SignatureVerificationError, match="enquiry response"):
            run(gateway(mode, FakeServer({"status": "00"})).status("ORDER123"))

    def test_verifies_the_shared_callback_vector(self, mode: str) -> None:
        callback = gateway(mode).handle_callback(CallbackRequest.from_json(signed()))
        assert callback.is_successful()
        assert callback.order_id == "ORD123456"
        assert callback.gateway_reference == "TRN0001"
        assert callback.raw["userRef1"] == "cart-9"
        assert callback.acknowledgement.status == 200

    def test_verifies_a_form_callback_and_json_numbers(self) -> None:
        payload_json = (
            '{"merchOrderId":"ORDER1","tranId":"T1","amount":1000.00,"currencyCode":104,'
            '"statusCode":"02"}'
        )
        body = urlencode(
            {
                "payload": base64.b64encode(payload_json.encode()).decode(),
                "checkSum": hmac_hex("ORDER1:T1:1000.00:104:02", SECRET).upper(),
            }
        )
        callback = gateway("sync").handle_callback(CallbackRequest(body=body))
        assert callback.status is PaymentStatus.FAILED
        assert callback.amount == "1000.00"

    @pytest.mark.parametrize(
        ("code", "status"),
        [
            ("01", PaymentStatus.PENDING),
            ("03", PaymentStatus.FAILED),
            ("04", PaymentStatus.EXPIRED),
            ("99", PaymentStatus.UNKNOWN),
        ],
    )
    def test_maps_status_codes(self, code: str, status: PaymentStatus) -> None:
        request = CallbackRequest.from_json(signed({"merchOrderId": "O1", "statusCode": code}))
        assert gateway("sync").handle_callback(request).status is status

    def test_verifies_the_return_redirect_with_plus_signs_turned_into_spaces(self) -> None:
        vector = fixture("aya_pay/callback_payload.json")
        payload = {**vector["payload"], "description": "Order 123456 >>>"}
        values = signed()
        values["payload"] = encode(payload)
        values["checkSum"] = hmac_hex(
            vector["checksum_string"].replace("Order 123456", "Order 123456 >>>"), SECRET
        )
        assert "+" in values["payload"]
        query = "&".join(f"{key}={value}" for key, value in values.items())
        request = CallbackRequest(query=query, body="payload=ignored")
        callback = gateway("sync").verify_redirect(request)
        assert callback.order_id == "ORD123456"
        encoded = "&".join(f"{key}={quote(value)}" for key, value in values.items())
        assert gateway("sync").verify_redirect(CallbackRequest(query=encoded)).is_successful()

    @pytest.mark.parametrize(
        "values",
        [
            {},
            {"payload": "not base64!", "checkSum": "x"},
            {"payload": base64.b64encode(b"[1]").decode(), "checkSum": "x"},
            {"payload": signed()["payload"], "checkSum": "0" * 64},
        ],
    )
    def test_rejects_bad_callbacks(self, values: dict[str, str]) -> None:
        with pytest.raises(SignatureVerificationError, match="AYA Pay callback checksum"):
            gateway("sync").handle_callback(CallbackRequest.from_json(values))

    @pytest.mark.parametrize(
        ("change", "field"),
        [
            ({"order_id": "ORD1"}, "order_id"),
            ({"order_id": "O" * 41}, "order_id"),
            ({"order_id": ""}, "order_id"),
            ({"amount": "10.50"}, "amount"),
            ({"amount": 0}, "amount"),
            ({"channel": ""}, "channel"),
            ({"method": "CARD"}, "method"),
            ({"method": None}, "method"),
            ({"return_url": "shop"}, "return_url"),
            ({"return_url": 1}, "return_url"),
            ({"description": 1}, "description"),
            ({"user_refs": "abc"}, "user_refs"),
            ({"user_refs": ["a", 1]}, "user_refs"),
            ({"user_refs": ["a"] * 6}, "user_refs"),
        ],
    )
    def test_validates_against_aya_rules(self, change: Any, field: str) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            AyaPay.validate(data(**change))
        assert info.value.errors[field]


class TestAyaPayConfig:
    def test_selects_the_endpoints(self) -> None:
        assert AyaPayConfig(app_key="k", app_secret="s").base_url == AyaPayConfig.SANDBOX_URL
        production = AyaPayConfig(app_key="k", app_secret="s", sandbox=False)
        assert production.base_url == AyaPayConfig.PRODUCTION_URL
        assert repr(production) == "AyaPayConfig(app_key='k', sandbox=False)"

    def test_reads_the_environment_with_the_aya_pgw_fallbacks(self, mode: str) -> None:
        env = {
            "AYA_PGW_APP_KEY": "k",
            "AYA_PGW_APP_SECRET": "s",
            "AYA_PGW_BASE_URL": "https://pgw.test/",
            "AYA_PAY_SANDBOX": "0",
        }
        config = AyaPayConfig.from_env(env)
        assert config.app_key == "k"
        assert config.base_url == "https://pgw.test"
        assert config.sandbox is False
        cls = AyaPay if mode == "sync" else AsyncAyaPay
        assert cls.from_env({**env, "AYA_PAY_APP_KEY": "new"}).config.app_key == "new"
        with pytest.raises(ConfigurationError) as info:
            AyaPayConfig.from_env({"AYA_PAY_APP_KEY": "k"})
        assert info.value.key == "app_secret"
