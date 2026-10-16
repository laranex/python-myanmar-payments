"""Walks the cross-SDK parity vectors shared with the PHP, Go and Node SDKs."""

from __future__ import annotations

import re
from typing import Any

import pytest

from python_myanmar_payments import (
    Amount,
    AsyncKbzPay,
    AsyncWavePay,
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
    WavePay,
    WavePayConfig,
    WavePayItem,
    WavePayPaymentData,
    YomaMmqr,
    YomaMmqrConfig,
)
from tests.helpers import FakeServer, fixture, gateway_config, make, run

VECTORS = fixture("parity/vectors.json")
SECRETS: dict[str, str] = VECTORS["secrets"]


def snake(name: str) -> str:
    """Python names fields in snake_case: ``timeToLiveMinutes`` is ``time_to_live_minutes``.

    A trailing number gets its own part too: ``additionalField3`` is ``additional_field_3``.
    """
    return re.sub(r"(?<!^)(?=[A-Z])|(?<=[a-z])(?=[0-9])", "_", name).lower()


CONFIG = VECTORS["config"]
CONFIG_ENV: dict[str, str] = CONFIG["env"]
CONFIGS: dict[str, Any] = {
    "kbz_pay": KbzPayConfig,
    "wave_pay": WavePayConfig,
    "aya_pay": AyaPayConfig,
    "yoma_mmqr": YomaMmqrConfig,
    "cyber_source": CyberSourceConfig,
}


def gateways() -> dict[str, Any]:
    return {
        "kbz_pay": KbzPay.from_env(CONFIG_ENV),
        "wave_pay": WavePay.from_env(CONFIG_ENV),
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
    headers = {"Content-Type": case["content_type"], **case.get("headers", {})}
    request = CallbackRequest(body=case["body"], headers=headers)
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
    names: tuple[str, ...] = ("base_url",)
    if isinstance(config, KbzPayConfig):
        names = ("api_url", "pwa_url")
    elif isinstance(config, WavePayConfig):
        names = ("base_url", "base_redirect_url")
    return {name: getattr(config, name) for name in names}


@pytest.mark.parametrize("gateway", list(CONFIGS))
def test_config_reads_the_production_urls(gateway: str) -> None:
    config = CONFIGS[gateway].from_env(CONFIG_ENV)
    assert urls(config) == CONFIG["urls"][gateway]
    if gateway != "cyber_source":
        assert config.timeout_seconds == CONFIG["seconds"]["timeout_in_seconds"]
    if gateway == "wave_pay":
        assert config.time_to_live_seconds == CONFIG["seconds"]["time_to_live_in_seconds"]


@pytest.mark.parametrize("gateway", list(CONFIGS))
def test_config_reads_the_uat_urls(gateway: str) -> None:
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


DEFAULT_TIMEOUT = CONFIG["default_timeout"]


@pytest.mark.parametrize("value", DEFAULT_TIMEOUT["values"], ids=repr)
@pytest.mark.parametrize("gateway", ["kbz_pay", "wave_pay", "aya_pay", "yoma_mmqr"])
def test_config_default_timeout(gateway: str, value: str | None) -> None:
    env = dict(CONFIG_ENV)
    if value is None:
        del env[DEFAULT_TIMEOUT["variable"]]
    else:
        env[DEFAULT_TIMEOUT["variable"]] = value
    config = CONFIGS[gateway].from_env(env)
    assert config.timeout_seconds == DEFAULT_TIMEOUT["timeout_in_seconds"]


def test_yoma_mmqr_token_cache_key() -> None:
    vector = VECTORS["yoma_mmqr_token_cache_key"]
    cache = MemoryTokenCache()
    server = FakeServer(
        {"access_token": "T", "expires_in": 3600}, {"qrString": "img", "refLabel": "R"}
    )
    YomaMmqr(
        gateway_config("yoma_mmqr", client_id=vector["client_id"], base_url=vector["base_url"]),
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
    wave = WavePayPaymentData(
        order_id="ORDER_1",
        callback_url="https://shop.test/cb",
        return_url="https://shop.test/done",
        description="Order",
        items=[WavePayItem("Tea", 1000)],
        amount=Amount.parse("1000.50"),
    )
    assert errors_of(WavePay.validate, wave)["amount"] == MESSAGES["whole_amounts_only"]

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
        order_id="ORDER_1",
        amount=1000,
        callback_url="https://shop.test/cb",
        time_to_live_minutes=0,
    )
    message = MESSAGES["kbz_time_to_live_range"].replace(
        "timeToLiveMinutes", snake("timeToLiveMinutes")
    )
    assert errors_of(KbzPay.validate, kbz)["time_to_live_minutes"] == message


OVERRIDES = VECTORS["wave_pay_request_overrides"]
OVERRIDE_FIELDS = {
    "merchant_name": "merchant_name",
    "time_to_live_in_seconds": "time_to_live_seconds",
}


def wave_override_data(case: dict[str, Any]) -> WavePayPaymentData:
    data = OVERRIDES["data"]
    return WavePayPaymentData(
        order_id=data["order_id"],
        callback_url=data["callback_url"],
        return_url=data["return_url"],
        description=data["description"],
        items=[WavePayItem(name, amount) for name, amount in data["items"]],
        merchant_reference_id=data["merchant_reference_id"],
        merchant_name=case["merchant_name"],
        time_to_live_seconds=case["time_to_live_in_seconds"],
    )


@pytest.mark.parametrize("case", OVERRIDES["cases"], ids=lambda case: repr(case["merchant_name"]))
def test_wave_pay_request_overrides(mode: str, case: dict[str, Any]) -> None:
    config = OVERRIDES["config"]
    server = FakeServer({"message": "success", "transaction_id": "T1"})
    run(
        make(
            mode,
            WavePay,
            AsyncWavePay,
            WavePayConfig(
                **gateway_config(
                    "wave_pay",
                    merchant_id=config["merchant_id"],
                    secret_key=config["secret_key"],
                    merchant_name=config["merchant_name"],
                    time_to_live_seconds=config["time_to_live_in_seconds"],
                )
            ),
            server,
        ).initiate(wave_override_data(case))
    )
    sent = server.last().json()
    assert {name: sent[name] for name in case["expected"]} == case["expected"]


@pytest.mark.parametrize(
    "case",
    OVERRIDES["invalid"],
    ids=lambda case: f"{case['setting']}: {case[case['setting']]!r}",
)
def test_wave_pay_invalid_overrides(case: dict[str, Any]) -> None:
    errors = errors_of(WavePay.validate, wave_override_data(case))
    assert list(errors) == [OVERRIDE_FIELDS[case["setting"]]]


WAVE_REQUEST = VECTORS["wave_pay_request"]


def wave_pay_data(data: dict[str, Any], **changes: Any) -> WavePayPaymentData:
    return WavePayPaymentData(
        **{
            "order_id": data["order_id"],
            "callback_url": data["callback_url"],
            "return_url": data["return_url"],
            "description": data["description"],
            "items": [WavePayItem(name, amount) for name, amount in data["items"]],
            "merchant_reference_id": data["merchant_reference_id"],
            "additional_field_3": data.get("additional_field_3"),
            "additional_field_4": data.get("additional_field_4"),
            "additional_field_5": data.get("additional_field_5"),
            **changes,
        }
    )


@pytest.mark.parametrize("case", WAVE_REQUEST["cases"], ids=lambda case: case["name"])
def test_wave_pay_request(mode: str, case: dict[str, Any]) -> None:
    expected = WAVE_REQUEST["expected"]
    response = WAVE_REQUEST["response"]
    config = WAVE_REQUEST["config"]
    server = FakeServer((response["status"], response["body"]))
    payment = run(
        make(
            mode,
            WavePay,
            AsyncWavePay,
            WavePayConfig(
                merchant_id=config["merchant_id"],
                secret_key=config["secret_key"],
                merchant_name=config["merchant_name"],
                base_url=config["base_url"],
                base_redirect_url=config["base_redirect_url"],
                time_to_live_seconds=config["time_to_live_in_seconds"],
            ),
            server,
        ).initiate(wave_pay_data(case["data"]))
    )
    sent = server.last()
    assert sent.method == expected["method"]
    assert sent.url == expected["url"]
    assert {name: sent.headers[name.lower()] for name in expected["headers"]} == expected["headers"]
    assert sent.json() == case["expected_body"]
    assert list(sent.json()) == list(case["expected_body"])
    assert payment.url == expected["redirect_url"]
    assert payment.gateway_reference == expected["gateway_reference"]


WAVE_LIMITS = VECTORS["wave_pay_limits"]


@pytest.mark.parametrize("case", WAVE_LIMITS["valid"], ids=lambda case: case["field"])
def test_wave_pay_limits_valid(case: dict[str, Any]) -> None:
    data = wave_pay_data(WAVE_LIMITS["data"], **{snake(case["field"]): case["value"]})
    WavePay.validate(data)
    assert getattr(data, snake(case["field"])) == case["value"]


@pytest.mark.parametrize("case", WAVE_LIMITS["invalid"], ids=lambda case: case["field"])
def test_wave_pay_limits_invalid(case: dict[str, Any]) -> None:
    field = snake(case["field"])
    errors = errors_of(
        WavePay.validate, wave_pay_data(WAVE_LIMITS["data"], **{field: case["value"]})
    )
    assert errors == {field: case["error"].replace(case["field"], field)}


KBZ_TIME_TO_LIVE = VECTORS["kbz_pay_time_to_live"]


@pytest.mark.parametrize("case", KBZ_TIME_TO_LIVE["cases"], ids=repr)
def test_kbz_pay_time_to_live(mode: str, case: dict[str, Any]) -> None:
    """The payment's time to live wins over the configured one."""
    env = {**CONFIG_ENV, KBZ_TIME_TO_LIVE["variable"]: case["config"]}
    server = FakeServer(
        {
            "Response": {
                "result": "SUCCESS",
                "code": "0",
                "msg": "success",
                "prepay_id": "PREPAY123",
                "qrCode": "qr",
            }
        }
    )
    payment = run(
        make(mode, KbzPay, AsyncKbzPay, KbzPayConfig.from_env(env), server).qr(
            KbzPayPaymentData(
                order_id="ORDER_1",
                amount=1000,
                callback_url="https://shop.test/cb",
                time_to_live_minutes=case["payment"],
            )
        )
    )
    biz = server.last().json()["Request"]["biz_content"]
    assert biz["timeout_express"] == case["timeout_express"]
    assert payment.expires_at is not None
