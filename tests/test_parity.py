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


CONFIG = VECTORS["config"]
CONFIG_ENV: dict[str, str] = CONFIG["env"]
CONFIGS: dict[str, Any] = {
    "kbz_pay": KbzPayConfig,
    "wave_money": WaveMoneyConfig,
    "aya_pay": AyaPayConfig,
    "yoma_mmqr": YomaMmqrConfig,
    "cyber_source": CyberSourceConfig,
}


def gateways() -> dict[str, Any]:
    return {
        "kbz_pay": KbzPay.from_env(CONFIG_ENV),
        "wave_money": WaveMoney.from_env(CONFIG_ENV),
        "aya_pay": AyaPay.from_env(CONFIG_ENV),
        "yoma_mmqr": YomaMmqr.from_env(CONFIG_ENV),
        "cyber_source": CyberSource.from_env(CONFIG_ENV),
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


def urls(config: Any) -> dict[str, str]:
    names = ("api_url", "pwa_url") if isinstance(config, KbzPayConfig) else ("base_url",)
    if isinstance(config, WaveMoneyConfig):
        names = ("base_url", "authenticate_url")
    return {name: getattr(config, name) for name in names}


@pytest.mark.parametrize("gateway", list(CONFIGS))
def test_config_defaults_to_production(gateway: str) -> None:
    config = CONFIGS[gateway].from_env(CONFIG_ENV)
    assert urls(config) == CONFIG["urls"][gateway]
    if gateway != "cyber_source":
        assert config.timeout_seconds == CONFIG["seconds"]["timeout_in_seconds"]
    if gateway == "wave_money":
        assert config.time_to_live_seconds == CONFIG["seconds"]["time_to_live_in_seconds"]


@pytest.mark.parametrize("gateway", list(CONFIGS))
def test_config_url_overrides(gateway: str) -> None:
    config = CONFIGS[gateway].from_env({**CONFIG_ENV, **CONFIG["uat"]["env"]})
    assert urls(config) == CONFIG["uat"]["urls"][gateway]


@pytest.mark.parametrize(
    "case",
    CONFIG["errors"],
    ids=lambda case: f"{case['gateway']}: {case['variable']}={case['value']!r}",
)
def test_config_errors(case: dict[str, Any]) -> None:
    env = dict(CONFIG_ENV)
    if case["value"] is None:
        del env[case["variable"]]
    else:
        env[case["variable"]] = case["value"]
    with pytest.raises(ConfigurationError) as info:
        CONFIGS[case["gateway"]].from_env(env)
    assert (info.value.gateway, info.value.key, str(info.value)) == (
        case["gateway"],
        case["key"],
        case["message"],
    )


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
            "api_version": "v1rc",
            "timeout_seconds": 30,
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


def test_invalid_configuration_message() -> None:
    vector = MESSAGES["configuration_invalid"]
    error = ConfigurationError(vector["gateway"], vector["key"], invalid=True)
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
