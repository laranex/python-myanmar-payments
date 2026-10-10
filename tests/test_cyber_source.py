from __future__ import annotations

from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import pytest

from python_myanmar_payments import (
    Amount,
    CallbackRequest,
    ConfigurationError,
    CyberSource,
    CyberSourceConfig,
    CyberSourcePaymentData,
    CyberSourceTransactionType,
    InvalidPaymentDataError,
    PaymentFlow,
    PaymentStatus,
    SignatureVerificationError,
)
from tests.conftest import Clock
from tests.helpers import hmac_base64

SECRET = "cs-secret"
CONFIG = CyberSourceConfig(profile_id="profile", access_key="access", secret_key=SECRET)
gateway = CyberSource(CONFIG)


def data(**changes: Any) -> CyberSourcePaymentData:
    values: dict[str, Any] = {
        "order_id": "ORDER_1",
        "amount": Amount.parse("10000.50"),
        "callback_url": "https://shop.test/cs/callback",
        "currency": "MMK",
        "transaction_type": CyberSourceTransactionType.SALE,
        "locale": "en-us",
    }
    return CyberSourcePaymentData(**{**values, **changes})


def sign(fields: dict[str, str]) -> str:
    names = fields["signed_field_names"].split(",")
    return hmac_base64(",".join(f"{name}={fields[name]}" for name in names), SECRET)


def result(**changes: str) -> dict[str, str]:
    fields = {
        "decision": "ACCEPT",
        "req_reference_number": "ORDER_1",
        "transaction_id": "TX1",
        "auth_amount": "10000.50",
        "req_amount": "10000.50",
        "signed_field_names": (
            "decision,req_reference_number,transaction_id,auth_amount,req_amount,signed_field_names"
        ),
        **changes,
    }
    return {**fields, "signature": sign(fields)}


class TestCyberSource:
    def test_signs_the_form_in_the_documented_field_order(self, clock: Clock) -> None:
        payment = gateway.initiate(
            data(
                return_url="https://shop.test/receipt",
                cancel_url="https://shop.test/cancel",
                currency="USD",
                transaction_type=CyberSourceTransactionType.AUTHORIZATION,
                locale="my-mm",
            )
        )
        values = payment.values()
        assert payment.flow is PaymentFlow.FORM
        assert payment.action == "https://secureacceptance.cybersource.com/pay"
        assert payment.enctype == "application/x-www-form-urlencoded"
        assert [field.name for field in payment.fields][-1] == "signature"
        assert values["signed_field_names"] == (
            "access_key,profile_id,transaction_uuid,signed_field_names,signed_date_time,"
            "locale,transaction_type,reference_number,amount,currency,"
            "override_custom_receipt_page,override_backoffice_post_url,"
            "override_custom_cancel_page"
        )
        assert values["signed_date_time"] == "2018-09-11T03:45:03Z"
        assert len(values["transaction_uuid"]) == 32
        assert values["amount"] == "10000.50"
        assert values["currency"] == "USD"
        assert values["locale"] == "my-mm"
        assert values["transaction_type"] == "authorization"
        assert values["override_custom_cancel_page"] == "https://shop.test/cancel"
        assert values["signature"] == sign(values)

    def test_sends_the_given_currency_locale_and_transaction_type(self) -> None:
        values = gateway.initiate(data(amount=Decimal("0"))).values()
        assert values["currency"] == "MMK"
        assert values["locale"] == "en-us"
        assert values["transaction_type"] == "sale"
        assert values["override_custom_receipt_page"] == ""
        assert values["amount"] == "0"

    def test_verifies_a_signed_result_and_keeps_only_signed_fields(self) -> None:
        fields = {**result(), "decision_extra": "x", "req_card_number": "xxxx1111"}
        callback = gateway.handle_callback(CallbackRequest(body=urlencode(fields)))
        assert callback.is_successful()
        assert callback.order_id == "ORDER_1"
        assert callback.gateway_status == "ACCEPT"
        assert callback.gateway_reference == "TX1"
        assert callback.amount == "10000.50"
        assert "req_card_number" not in callback.raw
        assert set(callback.raw) == {
            "decision",
            "req_reference_number",
            "transaction_id",
            "auth_amount",
            "req_amount",
            "signed_field_names",
            "signature",
        }

    def test_falls_back_to_the_requested_amount(self) -> None:
        fields = result(signed_field_names="decision,req_reference_number,req_amount")
        callback = gateway.handle_callback(CallbackRequest(query=urlencode(fields)))
        assert callback.amount == "10000.50"
        assert callback.gateway_reference is None

    @pytest.mark.parametrize(
        ("decision", "status"),
        [
            ("accept", PaymentStatus.SUCCESSFUL),
            ("REVIEW", PaymentStatus.PENDING),
            ("DECLINE", PaymentStatus.FAILED),
            ("ERROR", PaymentStatus.FAILED),
            ("CANCEL", PaymentStatus.CANCELED),
            ("OTHER", PaymentStatus.UNKNOWN),
        ],
    )
    def test_maps_decisions(self, decision: str, status: PaymentStatus) -> None:
        callback = gateway.handle_callback(
            CallbackRequest(body=urlencode(result(decision=decision)))
        )
        assert callback.status is status

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {**result(), "auth_amount": "1"},
            {**result(), "signature": "x"},
            result(signed_field_names="req_reference_number,signed_field_names"),
            result(signed_field_names="decision,signed_field_names"),
            {"signed_field_names": "decision,missing", "decision": "ACCEPT", "signature": "x"},
            {"signed_field_names": ",", "signature": "x"},
        ],
    )
    def test_rejects_unsigned_or_tampered_results(self, fields: dict[str, str]) -> None:
        with pytest.raises(SignatureVerificationError, match="CyberSource callback signature"):
            gateway.handle_callback(CallbackRequest(body=urlencode(fields)))

    def test_rejects_a_decision_added_next_to_signed_request_fields(self) -> None:
        fields = result(signed_field_names="req_reference_number,req_amount,signed_field_names")
        fields["decision"] = "ACCEPT"
        with pytest.raises(SignatureVerificationError):
            gateway.handle_callback(CallbackRequest(body=urlencode(fields)))

    @pytest.mark.parametrize(
        ("change", "field"),
        [
            ({"order_id": ""}, "order_id"),
            ({"order_id": "O" * 51}, "order_id"),
            ({"amount": "1234567890123.50"}, "amount"),
            ({"amount": -1}, "amount"),
            ({"amount": object()}, "amount"),
            ({"callback_url": ""}, "callback_url"),
            ({"callback_url": f"https://shop.test/{'a' * 240}"}, "callback_url"),
            ({"return_url": "nope"}, "return_url"),
            ({"return_url": 1}, "return_url"),
            ({"cancel_url": "javascript:alert(1)"}, "cancel_url"),
            ({"currency": "mmk"}, "currency"),
            ({"currency": 104}, "currency"),
            ({"locale": "en"}, "locale"),
            ({"currency": ""}, "currency"),
            ({"currency": None}, "currency"),
            ({"locale": " "}, "locale"),
            ({"locale": None}, "locale"),
            ({"transaction_type": ""}, "transaction_type"),
            ({"transaction_type": None}, "transaction_type"),
            ({"transaction_type": "refund"}, "transaction_type"),
        ],
    )
    def test_validates_against_the_field_reference(self, change: Any, field: str) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            CyberSource.validate(data(**change))
        assert info.value.errors[field]

    def test_requires_the_currency_locale_and_transaction_type(self) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            CyberSource.validate(data(currency="", locale="", transaction_type=""))
        assert dict(info.value.errors) == {
            "currency": "The currency field is required.",
            "locale": "The locale field is required.",
            "transaction_type": "The transaction_type field is required.",
        }

    def test_accepts_plain_transaction_type_strings(self) -> None:
        payment = gateway.initiate(data(transaction_type="sale,create_payment_token"))
        assert payment.field("transaction_type") == "sale,create_payment_token"


class TestCyberSourceConfig:
    def test_selects_the_endpoints_and_reads_the_environment(self) -> None:
        assert CONFIG.base_url == CyberSourceConfig.PRODUCTION_URL
        env = {
            "CYBER_SOURCE_PROFILE_ID": "p",
            "CYBER_SOURCE_ACCESS_KEY": "a",
            "CYBER_SOURCE_SECRET_KEY": "s",
        }
        config = CyberSourceConfig.from_env(env)
        assert config.base_url == CyberSourceConfig.PRODUCTION_URL
        assert repr(config) == "CyberSourceConfig(profile_id='p')"
        overridden = {**env, "CYBER_SOURCE_BASE_URL": "https://cs.test/"}
        assert CyberSource.from_env(overridden).config.base_url == "https://cs.test"
        with pytest.raises(ConfigurationError) as info:
            CyberSource.from_env({})
        assert info.value.key == "profile_id"
