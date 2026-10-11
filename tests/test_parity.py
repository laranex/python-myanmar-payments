"""Walks the cross-SDK parity vectors shared with the PHP, Go and Node SDKs."""

from __future__ import annotations

import re
from typing import Any

import pytest

from python_myanmar_payments import (
    Amount,
    AsyncKbzPay,
    AsyncWaveMoney,
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
from tests.helpers import FakeServer, fixture, make, run

VECTORS = fixture("parity/vectors.json")
SECRETS: dict[str, str] = VECTORS["secrets"]


def snake(name: str) -> str:
    """Python names fields in snake_case: ``timeToLiveMinutes`` is ``time_to_live_minutes``."""
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


DEFAULT_TIMEOUT = CONFIG["default_timeout"]


@pytest.mark.parametrize("value", DEFAULT_TIMEOUT["values"], ids=repr)
@pytest.mark.parametrize("gateway", ["kbz_pay", "wave_money", "aya_pay", "yoma_mmqr"])
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
        order_id="ORDER_1",
        amount=1000,
        callback_url="https://shop.test/cb",
        time_to_live_minutes=0,
    )
    message = MESSAGES["kbz_time_to_live_range"].replace(
        "timeToLiveMinutes", snake("timeToLiveMinutes")
    )
    assert errors_of(KbzPay.validate, kbz)["time_to_live_minutes"] == message


OVERRIDES = VECTORS["wave_money_request_overrides"]
OVERRIDE_FIELDS = {
    "merchant_name": "merchant_name",
    "time_to_live_in_seconds": "time_to_live_seconds",
}


def wave_override_data(case: dict[str, Any]) -> WaveMoneyPaymentData:
    data = OVERRIDES["data"]
    return WaveMoneyPaymentData(
        order_id=data["order_id"],
        callback_url=data["callback_url"],
        return_url=data["return_url"],
        description=data["description"],
        items=[WaveMoneyItem(name, amount) for name, amount in data["items"]],
        merchant_reference_id=data["merchant_reference_id"],
        merchant_name=case["merchant_name"],
        time_to_live_seconds=case["time_to_live_in_seconds"],
    )


@pytest.mark.parametrize("case", OVERRIDES["cases"], ids=lambda case: repr(case["merchant_name"]))
def test_wave_money_request_overrides(mode: str, case: dict[str, Any]) -> None:
    config = OVERRIDES["config"]
    server = FakeServer({"message": "success", "transaction_id": "T1"})
    run(
        make(
            mode,
            WaveMoney,
            AsyncWaveMoney,
            WaveMoneyConfig(
                merchant_id=config["merchant_id"],
                secret_key=config["secret_key"],
                merchant_name=config["merchant_name"],
                time_to_live_seconds=config["time_to_live_in_seconds"],
            ),
            server,
        ).initiate(wave_override_data(case))
    )
    sent = server.last().form()
    assert {name: sent[name] for name in case["expected"]} == case["expected"]


@pytest.mark.parametrize(
    "case",
    OVERRIDES["invalid"],
    ids=lambda case: f"{case['setting']}: {case[case['setting']]!r}",
)
def test_wave_money_invalid_overrides(case: dict[str, Any]) -> None:
    errors = errors_of(WaveMoney.validate, wave_override_data(case))
    assert list(errors) == [OVERRIDE_FIELDS[case["setting"]]]


KBZ_TIME_TO_LIVE = VECTORS["kbz_pay_time_to_live"]


@pytest.mark.parametrize("case", KBZ_TIME_TO_LIVE["cases"], ids=repr)
def test_kbz_pay_time_to_live(mode: str, case: dict[str, Any]) -> None:
    """The payment's time to live wins, then the configured one."""
    env = without(CONFIG_ENV, KBZ_TIME_TO_LIVE["variable"])
    if case["config"] is not None:
        env[KBZ_TIME_TO_LIVE["variable"]] = case["config"]
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


def without(env: dict[str, str], variable: str) -> dict[str, str]:
    return {key: value for key, value in env.items() if key != variable}


def missing_env(variable: str, value: str | None) -> dict[str, str]:
    env = without(CONFIG_ENV, variable)
    if value is not None:
        env[variable] = value
    return env


@pytest.mark.parametrize("value", KBZ_TIME_TO_LIVE["missing"]["configs"], ids=repr)
def test_kbz_pay_requires_a_time_to_live(mode: str, value: str | None) -> None:
    missing = KBZ_TIME_TO_LIVE["missing"]
    server = FakeServer()
    config = KbzPayConfig.from_env(missing_env(KBZ_TIME_TO_LIVE["variable"], value))
    with pytest.raises(InvalidPaymentDataError) as info:
        run(
            make(mode, KbzPay, AsyncKbzPay, config, server).qr(
                KbzPayPaymentData(
                    order_id="ORDER_1", amount=1000, callback_url="https://shop.test/cb"
                )
            )
        )
    field = snake(missing["field"])
    assert dict(info.value.errors) == {field: missing["message"].replace(missing["field"], field)}
    assert server.requests == []


WAVE_TIME_TO_LIVE_MISSING = VECTORS["wave_money_time_to_live_missing"]


@pytest.mark.parametrize("value", WAVE_TIME_TO_LIVE_MISSING["configs"], ids=repr)
def test_wave_money_requires_a_time_to_live(mode: str, value: str | None) -> None:
    missing = WAVE_TIME_TO_LIVE_MISSING
    server = FakeServer()
    config = WaveMoneyConfig.from_env(missing_env(missing["variable"], value))
    data = WaveMoneyPaymentData(
        order_id="100",
        callback_url="https://shop.test/cb",
        return_url="https://shop.test/done",
        description="Order 100",
        items=[WaveMoneyItem("Shoes", 1000)],
    )
    with pytest.raises(InvalidPaymentDataError) as info:
        run(make(mode, WaveMoney, AsyncWaveMoney, config, server).initiate(data))
    field = snake(missing["field"])
    assert dict(info.value.errors) == {field: missing["message"].replace(missing["field"], field)}
    assert server.requests == []
    assert data.merchant_reference_id is None or data.merchant_reference_id == ""
