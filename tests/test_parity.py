"""Walks the cross-SDK parity vectors shared with the PHP, Go and Node SDKs."""

from __future__ import annotations

import re
from typing import Any

import pytest

from python_myanmar_payments import (
    Amount,
    AyaPay,
    AyaPayConfig,
    AyaPayMethod,
    AyaPayPaymentData,
    CallbackRequest,
    ConfigurationError,
    CyberSource,
    CyberSourceConfig,
    FormField,
    FormPayment,
    InvalidPaymentDataError,
    KbzPay,
    KbzPayConfig,
    KbzPayPaymentData,
    MemoryTokenCache,
    PaymentCallback,
    SignatureVerificationError,
    WaveMoney,
    WaveMoneyConfig,
    WaveMoneyItem,
    WaveMoneyPaymentData,
    YomaMmqr,
    YomaMmqrConfig,
)
from tests.helpers import FakeServer, fixture

VECTORS = fixture("parity/vectors.json")
SECRETS: dict[str, str] = VECTORS["secrets"]


def snake(name: str) -> str:
    """Python names fields in snake_case: ``timeoutMinutes`` is ``timeout_minutes``."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def gateways() -> dict[str, Any]:
    return {
        "kbz_pay": KbzPay(
            {"app_id": "kp123", "app_key": SECRETS["kbz_pay_app_key"], "merchant_code": "100001"}
        ),
        "wave_money": WaveMoney(
            {
                "merchant_id": "merchant",
                "secret_key": SECRETS["wave_money_secret_key"],
                "merchant_name": "Shop",
            }
        ),
        "aya_pay": AyaPay({"app_key": "app-key", "app_secret": SECRETS["aya_pay_app_secret"]}),
        "yoma_mmqr": YomaMmqr(
            {
                "merchant_id": "M1",
                "client_id": "client",
                "client_secret": "secret",
                "webhook_hash_key": SECRETS["yoma_mmqr_webhook_hashkey"],
            }
        ),
        "cyber_source": CyberSource(
            {
                "profile_id": "profile",
                "access_key": "access",
                "secret_key": SECRETS["cyber_source_secret_key"],
            }
        ),
    }


CALLBACKS = [
    pytest.param(gateway, case, id=f"{gateway}: {case['name']}")
    for gateway, cases in VECTORS["callbacks"].items()
    for case in cases
]


@pytest.mark.parametrize(("gateway", "case"), CALLBACKS)
def test_callbacks(gateway: str, case: dict[str, Any]) -> None:
    request = CallbackRequest(body=case["body"], headers={"Content-Type": case["content_type"]})
    expected = case["expected"]
    handle = gateways()[gateway].handle_callback
    if not expected["valid"]:
        with pytest.raises(SignatureVerificationError):
            handle(request)
        return
    callback: PaymentCallback = handle(request)
    assert callback.order_id == expected["order_id"]
    assert callback.status == expected["status"]
    assert callback.gateway_status == expected["gateway_status"]
    assert callback.gateway_reference == expected.get("gateway_reference")
    assert callback.amount == expected.get("amount")


@pytest.mark.parametrize("case", VECTORS["amount_parse"], ids=lambda case: repr(case["input"]))
def test_amount_parse(case: dict[str, Any]) -> None:
    if case["value"] is None:
        with pytest.raises(InvalidPaymentDataError):
            Amount.parse(case["input"])
    else:
        assert str(Amount.parse(case["input"])) == case["value"]


def test_amount_parse_error() -> None:
    vector = VECTORS["amount_parse_error"]
    with pytest.raises(InvalidPaymentDataError) as error:
        Amount.parse(vector["input"])
    assert dict(error.value.errors) == vector["errors"]


@pytest.mark.parametrize(
    "case", VECTORS["amount_equals"], ids=lambda case: f"{case['amount']} vs {case['other']!r}"
)
def test_amount_equals(case: dict[str, Any]) -> None:
    assert Amount.parse(case["amount"]).equals(case["other"]) is case["equal"]


ENV = {
    "KBZ_PAY": {
        "KBZ_PAY_APP_ID": "a",
        "KBZ_PAY_APP_KEY": "k",
        "KBZ_PAY_MERCHANT_CODE": "m",
    },
    "WAVE_MONEY": {
        "WAVE_MONEY_MERCHANT_ID": "m",
        "WAVE_MONEY_SECRET_KEY": "s",
        "WAVE_MONEY_MERCHANT_NAME": "n",
    },
    "AYA_PAY": {"AYA_PAY_APP_KEY": "k", "AYA_PAY_APP_SECRET": "s"},
    "YOMA_MMQR": {
        "YOMA_MMQR_MERCHANT_ID": "m",
        "YOMA_MMQR_CLIENT_ID": "c",
        "YOMA_MMQR_CLIENT_SECRET": "s",
        "YOMA_MMQR_WEBHOOK_HASHKEY": "h",
    },
    "CYBER_SOURCE": {
        "CYBER_SOURCE_PROFILE_ID": "p",
        "CYBER_SOURCE_ACCESS_KEY": "a",
        "CYBER_SOURCE_SECRET_KEY": "s",
    },
}
CONFIGS: dict[str, Any] = {
    "KBZ_PAY": KbzPayConfig,
    "WAVE_MONEY": WaveMoneyConfig,
    "AYA_PAY": AyaPayConfig,
    "YOMA_MMQR": YomaMmqrConfig,
    "CYBER_SOURCE": CyberSourceConfig,
}


@pytest.mark.parametrize("case", VECTORS["sandbox"], ids=lambda case: repr(case["value"]))
@pytest.mark.parametrize("prefix", list(CONFIGS))
def test_sandbox(prefix: str, case: dict[str, Any]) -> None:
    env = {**ENV[prefix], f"{prefix}_SANDBOX": case["value"]}
    assert CONFIGS[prefix].from_env(env).sandbox is case["sandbox"]
    # The same text given as the `sandbox` setting reads the same way.
    options = {key.removeprefix(f"{prefix}_").lower(): value for key, value in ENV[prefix].items()}
    options = {
        ("webhook_hash_key" if key == "webhook_hashkey" else key): value
        for key, value in options.items()
    }
    assert CONFIGS[prefix](**options, sandbox=case["value"]).sandbox is case["sandbox"]


def test_yoma_mmqr_token_cache_key() -> None:
    vector = VECTORS["yoma_mmqr_token_cache_key"]
    cache = MemoryTokenCache()
    server = FakeServer(
        {"access_token": "T", "expires_in": 3600}, {"qrString": "img", "refLabel": "R"}
    )
    YomaMmqr(
        {
            "merchant_id": "M1",
            "client_id": vector["client_id"],
            "client_secret": "secret",
            "webhook_hash_key": "h",
            "base_url": vector["base_url"],
        },
        token_cache=cache,
        http_client=server.client(),
    ).renew_qr("ORDER_1")
    assert cache.get(vector["key"]) == "T"


def test_form_html() -> None:
    vector = VECTORS["form_html"]
    payment = FormPayment(
        order_id=vector["order_id"],
        action=vector["action"],
        fields=tuple(FormField(name, value) for name, value in vector["fields"]),
        enctype=vector["enctype"],
    )
    assert payment.to_html() == vector["html"]


MESSAGES = VECTORS["messages"]


def test_invalid_payment_data_message() -> None:
    vector = MESSAGES["invalid_payment_data"]
    assert str(InvalidPaymentDataError(vector["errors"])) == vector["message"]


def test_configuration_message() -> None:
    vector = MESSAGES["configuration"]
    error = ConfigurationError(vector["gateway"], vector["key"])
    assert (str(error), error.gateway, error.key) == (
        vector["message"],
        vector["gateway"],
        vector["key"],
    )


def errors_of(validate: Any, data: Any) -> dict[str, str]:
    with pytest.raises(InvalidPaymentDataError) as error:
        validate(data)
    return dict(error.value.errors)


def test_amount_and_timeout_messages() -> None:
    wave = WaveMoneyPaymentData(
        order_id="ORDER_1",
        callback_url="https://shop.test/cb",
        return_url="https://shop.test/done",
        description="Order",
        items=[WaveMoneyItem("Tea", 1000)],
        amount=Amount.parse("1000.50"),
    )
    assert errors_of(WaveMoney.validate, wave)["amount"] == MESSAGES["whole_amounts_only"]

    aya = AyaPayPaymentData(
        order_id="ORDER_1",
        amount=Amount.parse("1000.50"),
        channel="kbz_pay",
        method=AyaPayMethod.QR,
    )
    assert errors_of(AyaPay.validate, aya)["amount"] == MESSAGES["aya_whole_amounts_only"]

    kbz = KbzPayPaymentData(
        order_id="ORDER_1", amount=Amount.parse("1000.505"), callback_url="https://shop.test/cb"
    )
    assert errors_of(KbzPay.validate, kbz)["amount"] == MESSAGES["kbz_decimals"]

    kbz = KbzPayPaymentData(
        order_id="ORDER_1", amount=1000, callback_url="https://shop.test/cb", timeout_minutes=0
    )
    message = MESSAGES["kbz_timeout_zero"].replace("timeoutMinutes", snake("timeoutMinutes"))
    assert errors_of(KbzPay.validate, kbz)["timeout_minutes"] == message
