from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest

from python_myanmar_payments import (
    Amount,
    ApiError,
    AsyncKbzPay,
    CallbackRequest,
    ConfigurationError,
    InvalidPaymentDataError,
    KbzPay,
    KbzPayConfig,
    KbzPayPaymentData,
    KbzPaySigner,
    PaymentStatus,
    SignatureVerificationError,
    kbz_pay,
)
from tests.conftest import Clock
from tests.helpers import NOW, FakeServer, fixture, make, run

CONFIG: dict[str, Any] = {
    "app_id": "kp123",
    "app_key": "secret-key",
    "merchant_code": "100001",
    "timeout_seconds": 30,
    "api_url": "https://kbz.test",
}

signer = KbzPaySigner("secret-key")


def gateway(mode: str, server: FakeServer | None = None) -> Any:
    return make(mode, KbzPay, AsyncKbzPay, KbzPayConfig(**CONFIG), server)


def precreate(**extra: Any) -> dict[str, Any]:
    return {
        "Response": {
            "result": "SUCCESS",
            "code": "0",
            "msg": "success",
            "prepay_id": "PREPAY123",
            **extra,
        }
    }


def data(**changes: Any) -> KbzPayPaymentData:
    values: dict[str, Any] = {
        "order_id": "ORDER_1",
        "amount": Amount.kyat(1000),
        "callback_url": "https://shop.test/kbz/callback",
    }
    return KbzPayPaymentData(**{**values, **changes})


def request_of(server: FakeServer) -> dict[str, Any]:
    request: dict[str, Any] = server.last().json()["Request"]
    return request


class TestKbzPaySigner:
    def test_builds_the_sign_string_exactly_as_the_kbz_docs_example(self) -> None:
        vector = fixture("kbz_pay/sign_string.json")
        assert KbzPaySigner("any").sign_string(vector["fields"]) == vector["expected"]

    def test_does_not_url_encode_values(self) -> None:
        assert (
            KbzPaySigner("k").sign_string(
                {"notify_url": "https://a.test/x y", "callback_info": "title%3Diphonex"}
            )
            == "callback_info=title%3Diphonex&notify_url=https://a.test/x y"
        )

    def test_skips_empty_and_non_scalar_fields_and_signs_booleans_as_text(self) -> None:
        fields = {"b": True, "a": "", "c": None, "d": {"x": 1}, "e": False, "f": 7}
        assert KbzPaySigner("k").sign_string(fields) == "b=true&e=false&f=7"

    def test_signs_with_the_app_key_uppercase_and_verifies_case_insensitively(self) -> None:
        fields: dict[str, object] = {"a": "1", "sign_type": "SHA256"}
        sign = signer.sign(fields)
        assert len(sign) == 64
        assert sign == sign.upper()
        assert signer.verify({**fields, "sign": sign.lower()})
        assert not signer.verify({**fields, "sign": "X"})
        assert not signer.verify({**fields, "sign": 1})
        assert not signer.verify(fields)

    def test_rejects_fields_with_a_nested_value_even_when_the_sign_matches(self) -> None:
        fields: dict[str, object] = {"a": "1", "list": None}
        sign = signer.sign(fields)
        assert signer.verify({**fields, "sign": sign})
        assert not signer.verify({**fields, "list": [1], "sign": sign})
        assert not signer.verify({**fields, "nested": {"x": 1}, "sign": sign})


class TestKbzPay:
    def test_sends_a_signed_precreate_request_and_returns_the_pwa_url(
        self, mode: str, clock: Clock
    ) -> None:
        server = FakeServer(precreate())
        payment = run(gateway(mode, server).pwa(data()))

        request = request_of(server)
        biz = request["biz_content"]
        assert server.last().url == "https://kbz.test/precreate"
        assert server.last().headers["content-type"] == "application/json"
        assert request["method"] == "kbz.payment.precreate"
        assert request["version"] == "1.0"
        assert request["notify_url"] == "https://shop.test/kbz/callback"
        assert request["timestamp"] == str(NOW)
        assert request["sign_type"] == "SHA256"
        assert biz == {
            "appid": "kp123",
            "merch_code": "100001",
            "merch_order_id": "ORDER_1",
            "trade_type": "PWAAPP",
            "total_amount": "1000",
            "trans_currency": "MMK",
        }
        common = {key: value for key, value in request.items() if key != "biz_content"}
        assert request["sign"] == signer.sign({**common, **biz})

        assert payment.flow == "redirect"
        assert payment.url.startswith(f"{KbzPayConfig.PRODUCTION_PWA_URL}?")
        assert payment.gateway_reference == "PREPAY123"
        assert payment.order_id == "ORDER_1"
        assert payment.raw["prepay_id"] == "PREPAY123"

        query = dict(parse_qsl(payment.url.split("?", 1)[1]))
        sign = query.pop("sign")
        assert list(query) == ["appid", "merch_code", "nonce_str", "prepay_id", "timestamp"]
        assert query["nonce_str"] == request["nonce_str"]
        assert sign == signer.sign(query)

    def test_returns_the_qr_payload(self, mode: str) -> None:
        server = FakeServer(precreate(qrCode="kbzpay://qr/abc"))
        payment = run(gateway(mode, server).qr(data()))
        assert payment.qr_string == "kbzpay://qr/abc"
        assert payment.qr_image is None
        assert payment.reference == "PREPAY123"
        assert payment.expires_at is None
        assert payment.qr_image_data_uri() is None
        assert request_of(server)["biz_content"]["trade_type"] == "PAY_BY_QRCODE"

    def test_sets_the_qr_expiry_from_timeout_minutes(self, mode: str, clock: Clock) -> None:
        server = FakeServer(precreate(qrCode="qr"))
        payment = run(gateway(mode, server).qr(data(timeout_minutes=30)))
        assert payment.expires_at == datetime.fromtimestamp(NOW + 30 * 60, tz=timezone.utc)

    def test_fails_when_kbz_returns_no_qr_code(self, mode: str) -> None:
        with pytest.raises(ApiError, match=r"^KBZ Pay did not return a QR code\.$"):
            run(gateway(mode, FakeServer(precreate())).qr(data()))

    def test_returns_signed_order_info_for_the_in_app_flow(self, mode: str, clock: Clock) -> None:
        server = FakeServer(precreate())
        payment = run(gateway(mode, server).app(data()))
        nonce = request_of(server)["nonce_str"]
        assert len(nonce) == 32
        assert payment.order_info == (
            f"appid=kp123&merch_code=100001&nonce_str={nonce}&prepay_id=PREPAY123&timestamp={NOW}"
        )
        assert payment.sign_type == "SHA256"
        assert payment.sign == signer.sign(
            {
                "appid": "kp123",
                "merch_code": "100001",
                "nonce_str": nonce,
                "prepay_id": "PREPAY123",
                "timestamp": str(NOW),
            }
        )
        assert payment.to_dict() == {
            "orderId": "ORDER_1",
            "orderInfo": payment.order_info,
            "sign": payment.sign,
            "signType": "SHA256",
        }

    def test_sends_optional_fields_inside_biz_content_and_keeps_decimals(self, mode: str) -> None:
        server = FakeServer(precreate())
        run(
            gateway(mode, server).pwa(
                data(
                    amount=Amount.parse("1000.50"),
                    title="Shoes",
                    timeout_minutes=30,
                    callback_info="cart=9",
                )
            )
        )
        biz = request_of(server)["biz_content"]
        assert biz["title"] == "Shoes"
        assert biz["timeout_express"] == "30m"
        assert biz["callback_info"] == "cart%3D9"
        assert biz["total_amount"] == "1000.50"

    @pytest.mark.parametrize(
        ("amount", "sent"),
        [(2500, "2500"), ("0100.5", "100.5"), (Amount.parse("10.25"), "10.25")],
    )
    def test_accepts_ints_and_decimal_text(self, mode: str, amount: Any, sent: str) -> None:
        server = FakeServer(precreate())
        run(gateway(mode, server).pwa(data(amount=amount)))
        assert request_of(server)["biz_content"]["total_amount"] == sent

    def test_raises_the_gateway_error_when_precreate_fails(self, mode: str) -> None:
        server = FakeServer(
            {"Response": {"result": "FAIL", "code": "ORDER_ID_USED", "msg": "Order id used"}}
        )
        with pytest.raises(ApiError) as info:
            run(gateway(mode, server).pwa(data()))
        assert info.value.gateway_code == "ORDER_ID_USED"
        assert info.value.gateway_message == "Order id used"
        assert info.value.http_status == 200
        assert str(info.value) == "KBZ Pay precreate failed: [ORDER_ID_USED] Order id used"

    def test_reports_a_kbz_code_without_a_message(self, mode: str) -> None:
        server = FakeServer({"Response": {"result": "FAIL", "code": "AOP08508"}})
        with pytest.raises(ApiError) as info:
            run(gateway(mode, server).pwa(data()))
        assert str(info.value) == "KBZ Pay precreate failed: [AOP08508]"
        assert info.value.gateway_message is None

    def test_reports_http_failures_without_a_kbz_code(self, mode: str) -> None:
        with pytest.raises(ApiError) as info:
            run(gateway(mode, FakeServer((502, "Bad gateway"))).pwa(data()))
        assert str(info.value) == "KBZ Pay precreate failed with HTTP 502."
        assert info.value.gateway_code is None
        assert info.value.http_status == 502
        assert info.value.raw == {}

    def test_fails_when_precreate_returns_no_prepay_id(self, mode: str) -> None:
        server = FakeServer({"Response": {"result": "SUCCESS", "code": "0"}})
        with pytest.raises(ApiError, match="did not return a prepay_id"):
            run(gateway(mode, server).app(data()))

    def test_reports_an_unreachable_gateway(self, mode: str) -> None:
        server = FakeServer(httpx.ConnectError("connection refused"))
        with pytest.raises(ApiError) as info:
            run(gateway(mode, server).status("ORDER_1"))
        assert str(info.value) == "Could not reach https://kbz.test/queryorder: connection refused"
        assert isinstance(info.value.__cause__, httpx.ConnectError)
        assert info.value.http_status == 0

    def test_validates_before_sending_anything(self, mode: str) -> None:
        server = FakeServer()
        with pytest.raises(InvalidPaymentDataError):
            run(gateway(mode, server).pwa(data(order_id="ORDER-1")))
        assert server.requests == []

    @pytest.mark.parametrize(
        ("trade_status", "status"),
        [
            ("PAY_SUCCESS", PaymentStatus.SUCCESSFUL),
            (" PAY_SUCCESS", PaymentStatus.SUCCESSFUL),
            ("WAIT_PAY", PaymentStatus.PENDING),
            ("PAYING", PaymentStatus.PENDING),
            ("PAY_FAILED", PaymentStatus.FAILED),
            ("ORDER_CLOSED", PaymentStatus.CANCELED),
            ("ORDER_EXPIRED", PaymentStatus.EXPIRED),
            ("SOMETHING_NEW", PaymentStatus.UNKNOWN),
        ],
    )
    def test_maps_the_queryorder_trade_status(
        self, mode: str, trade_status: str, status: PaymentStatus
    ) -> None:
        server = FakeServer(
            {
                "Response": {
                    "result": "SUCCESS",
                    "code": "0",
                    "merch_order_id": "ORDER_1",
                    "trade_status": trade_status,
                    "total_amount": "1000",
                    "mm_order_id": "MM1",
                }
            }
        )
        result = run(gateway(mode, server).status("ORDER_1"))
        assert server.last().url == "https://kbz.test/queryorder"
        assert request_of(server)["method"] == "kbz.payment.queryorder"
        assert request_of(server)["version"] == "3.0"
        assert request_of(server)["biz_content"] == {
            "appid": "kp123",
            "merch_code": "100001",
            "merch_order_id": "ORDER_1",
        }
        assert result.order_id == "ORDER_1"
        assert result.status is status
        assert result.gateway_status == trade_status.strip()
        assert result.gateway_reference == "MM1"
        assert result.amount == "1000"
        assert result.is_successful() is (status is PaymentStatus.SUCCESSFUL)

    def test_keeps_the_queried_order_id_and_the_exact_amount_text(self, mode: str) -> None:
        body = (
            '{"Response":{"result":"SUCCESS","code":0,"trade_status":"WAIT_PAY",'
            '"total_amount":1000.50}}'
        )
        result = run(gateway(mode, FakeServer(body)).status("ORDER_9"))
        assert result.order_id == "ORDER_9"
        assert result.amount == "1000.50"
        assert result.gateway_reference is None
        assert result.status is PaymentStatus.PENDING
        assert str(result.raw["total_amount"]) == "1000.50"
        assert result.raw["code"] == "0"

    def test_is_safe_for_concurrent_async_use(self) -> None:
        body = '{"Response":{"result":"SUCCESS","code":"0","prepay_id":"P","qrCode":"qr"}}'

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=body)

        kbz = AsyncKbzPay(
            KbzPayConfig(**CONFIG),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        async def many() -> list[Any]:
            return list(await asyncio.gather(*(kbz.qr(data()) for _ in range(50))))

        assert all(payment.qr_string == "qr" for payment in asyncio.run(many()))

    def signed_callback(self, **fields: Any) -> CallbackRequest:
        signed = {**fields, "sign_type": "SHA256"}
        return CallbackRequest.from_json({"Request": {**signed, "sign": signer.sign(signed)}})

    def test_verifies_a_callback_and_acknowledges_it_with_plain_success(self, mode: str) -> None:
        callback = gateway(mode).handle_callback(
            self.signed_callback(
                appid="kp123",
                notify_time=1536637503,
                merch_code="100001",
                merch_order_id="ORDER_1",
                mm_order_id="0112345",
                total_amount="1000",
                trans_currency="MMK",
                trade_status="PAY_SUCCESS",
                callback_info="title%3Diphonex",
                nonce_str="abc",
            )
        )
        assert callback.is_successful()
        assert callback.order_id == "ORDER_1"
        assert callback.gateway_reference == "0112345"
        assert callback.amount == "1000"
        assert callback.gateway_status == "PAY_SUCCESS"
        assert callback.raw["notify_time"] == "1536637503"
        assert callback.raw["merch_order_id"] == "ORDER_1"
        ack = callback.acknowledgement
        assert ack.status == 200
        assert ack.body == "success"
        assert ack.headers == {"Content-Type": "text/plain"}

    def test_verifies_numbers_with_the_exact_text_kbz_sent(self) -> None:
        fields = {
            "merch_order_id": "ORDER_1",
            "total_amount": "1000.50",
            "trade_status": "PAY_SUCCESS",
            "notify_time": "1536637503",
        }
        sign = signer.sign(fields)
        body = (
            '{"Request":{"merch_order_id":"ORDER_1","total_amount":1000.50,'
            '"trade_status":"PAY_SUCCESS","notify_time":1536637503,"sign_type":"SHA256",'
            f'"sign":"{sign}"}}}}'
        )
        callback = gateway("sync").handle_callback(CallbackRequest(body=body.encode()))
        assert callback.amount == "1000.50"

    def test_accepts_a_callback_that_is_not_wrapped_in_request(self) -> None:
        fields = {"merch_order_id": "ORDER_1", "trade_status": "WAIT_PAY"}
        callback = gateway("sync").handle_callback(
            CallbackRequest.from_json({**fields, "sign": signer.sign(fields)})
        )
        assert callback.status is PaymentStatus.PENDING
        assert callback.gateway_reference is None
        assert callback.amount is None

    def test_rejects_a_tampered_callback(self) -> None:
        fields: dict[str, Any] = {
            "merch_order_id": "ORDER_1",
            "total_amount": "1000",
            "trade_status": "PAY_SUCCESS",
            "sign_type": "SHA256",
        }
        fields["sign"] = signer.sign(fields)
        fields["total_amount"] = "1"
        with pytest.raises(SignatureVerificationError) as info:
            gateway("sync").handle_callback(CallbackRequest.from_json({"Request": fields}))
        assert info.value.raw["Request"]["total_amount"] == "1"
        with pytest.raises(SignatureVerificationError):
            gateway("sync").handle_callback(CallbackRequest())
        with pytest.raises(SignatureVerificationError):
            gateway("sync").handle_callback(CallbackRequest.from_json({"Request": {}}))

    @pytest.mark.parametrize(
        ("change", "field"),
        [
            ({"order_id": "ORDER-1"}, "order_id"),
            ({"order_id": "a" * 41}, "order_id"),
            ({"order_id": ""}, "order_id"),
            ({"order_id": None}, "order_id"),
            ({"amount": Amount.kyat(0)}, "amount"),
            ({"amount": Amount.parse("1000.505")}, "amount"),
            ({"amount": -5}, "amount"),
            ({"amount": 10.5}, "amount"),
            ({"amount": None}, "amount"),
            ({"callback_url": ""}, "callback_url"),
            ({"callback_url": None}, "callback_url"),
            ({"callback_url": "https://shop.test/cb?x=1"}, "callback_url"),
            ({"callback_url": "/cb"}, "callback_url"),
            ({"callback_url": f"https://shop.test/{'a' * 500}"}, "callback_url"),
            ({"title": 5}, "title"),
            ({"timeout_minutes": 121}, "timeout_minutes"),
            ({"timeout_minutes": 0}, "timeout_minutes"),
            ({"timeout_minutes": -1}, "timeout_minutes"),
            ({"timeout_minutes": 1.5}, "timeout_minutes"),
            ({"timeout_minutes": True}, "timeout_minutes"),
            ({"callback_info": "=" * 200}, "callback_info"),
            ({"callback_info": 7}, "callback_info"),
        ],
    )
    def test_validates_the_order_against_kbz_limits(self, change: Any, field: str) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            KbzPay.validate(data(**change))
        assert info.value.errors[field]

    def test_accepts_two_decimal_places_and_http_callback_urls(self) -> None:
        KbzPay.validate(data(amount=Amount.parse("1000.55"), callback_url="http://shop.test/cb"))
        with pytest.raises(InvalidPaymentDataError) as info:
            KbzPay.validate(data(amount=Amount.parse("1000.505")))
        assert "KBZ Pay accepts at most 2 decimal places" in info.value.errors["amount"]


class TestKbzPayConfig:
    def test_names_a_missing_key(self) -> None:
        with pytest.raises(ConfigurationError) as info:
            KbzPayConfig(app_id="a", app_key="", merchant_code="c", timeout_seconds=30)
        assert info.value.gateway == "kbz_pay"
        assert info.value.key == "app_key"
        assert str(info.value) == "The kbz_pay configuration is missing [app_key]."

    def test_normalizes_the_pwa_url_so_the_query_always_follows_the_hash(self) -> None:
        for pwa_url in (
            "https://static.kbzpay.com/pgw/uat/pwa/#",
            "https://static.kbzpay.com/pgw/uat/pwa/#/",
        ):
            config = KbzPayConfig(**{**CONFIG, "pwa_url": pwa_url})
            assert config.pwa_url == "https://static.kbzpay.com/pgw/uat/pwa/#/"

    def test_defaults_to_the_production_endpoints(self) -> None:
        production = KbzPayConfig(app_id="a", app_key="b", merchant_code="c", timeout_seconds=30)
        assert production.api_url == KbzPayConfig.PRODUCTION_API_URL
        assert production.pwa_url == KbzPayConfig.PRODUCTION_PWA_URL
        assert production.timeout_seconds == 30
        proxied = KbzPayConfig(**{**CONFIG, "api_url": "https://proxy.test/kbz/"})
        assert proxied.api_url == "https://proxy.test/kbz"
        assert repr(production) == "KbzPayConfig(app_id='a')"

    def test_requires_a_whole_number_timeout(self) -> None:
        with pytest.raises(ConfigurationError) as info:
            KbzPayConfig(app_id="a", app_key="b", merchant_code="c")
        assert str(info.value) == "The kbz_pay configuration is missing [timeout_in_seconds]."
        with pytest.raises(ConfigurationError) as info:
            KbzPayConfig(app_id="a", app_key="b", merchant_code="c", timeout_seconds=0)
        assert str(info.value) == (
            "The kbz_pay configuration [timeout_in_seconds] must be a whole number greater than 0."
        )

    def test_reads_the_environment(self, mode: str) -> None:
        env = {
            "KBZ_PAY_APP_ID": "kp1",
            "KBZ_PAY_APP_KEY": "key",
            "KBZ_PAY_MERCHANT_CODE": "200",
            "MYANMAR_PAYMENTS_HTTP_TIMEOUT": "15",
            "KBZ_PAY_BASE_URL": "https://api.test/",
            "KBZ_PAY_PWA_BASE_REDIRECT_URL": "https://pwa.test/#",
        }
        config = KbzPayConfig.from_env(env)
        assert config.app_id == "kp1"
        assert config.app_key == "key"
        assert config.merchant_code == "200"
        assert config.timeout_seconds == 15
        assert config.api_url == "https://api.test"
        assert config.pwa_url == "https://pwa.test/#/"
        cls = KbzPay if mode == "sync" else AsyncKbzPay
        assert cls.from_env(env).config.app_id == "kp1"
        with pytest.raises(ConfigurationError) as info:
            cls.from_env({})
        assert info.value.key == "app_id"

    def test_reads_os_environ_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KBZ_PAY_APP_ID", "from-process")
        monkeypatch.setenv("KBZ_PAY_APP_KEY", "k")
        monkeypatch.setenv("KBZ_PAY_MERCHANT_CODE", "m")
        monkeypatch.setenv("MYANMAR_PAYMENTS_HTTP_TIMEOUT", "30")
        assert KbzPayConfig.from_env().app_id == "from-process"
        assert KbzPay.from_env().config.app_id == "from-process"

    def test_exports_the_gateway_from_its_module(self) -> None:
        assert kbz_pay.KbzPay is KbzPay
        assert kbz_pay.AsyncKbzPay is AsyncKbzPay
        assert isinstance(KbzPay(KbzPayConfig(**CONFIG)).signer, KbzPaySigner)
