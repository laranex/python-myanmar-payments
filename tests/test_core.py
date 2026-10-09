from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

import httpx
import pytest

from python_myanmar_payments import (
    Acknowledgement,
    ApiError,
    AppPayment,
    AsyncKbzPay,
    AsyncTokenCache,
    CallbackRequest,
    ConfigurationError,
    FormField,
    FormPayment,
    InvalidPaymentDataError,
    KbzPay,
    KbzPayConfig,
    MemoryTokenCache,
    PaymentCallback,
    PaymentError,
    PaymentFlow,
    PaymentStatus,
    PaymentStatusResult,
    QrPayment,
    RedirectPayment,
    SignatureVerificationError,
    TokenCache,
    __version__,
    resolve_status,
)
from python_myanmar_payments._callback import lossless_body
from python_myanmar_payments._json import JsonNumber, parse_object, to_plain
from python_myanmar_payments._support import (
    current_time,
    decode_base64,
    env_int,
    env_sandbox,
    query_escape,
)
from python_myanmar_payments._validate import AmountRule, Validator
from python_myanmar_payments._values import get, scalar_string

KBZ = KbzPayConfig(app_id="a", app_key="b", merchant_code="c", api_url="https://kbz.test")


class QueryDict:
    """Django's QueryDict: indexing returns the last value, lists() all of them."""

    def __init__(self, items: dict[str, list[str]]) -> None:
        self._items = items

    def lists(self) -> list[tuple[str, list[str]]]:
        return list(self._items.items())


class MultiItems:
    """Starlette's QueryParams."""

    def multi_items(self) -> list[tuple[str, str]]:
        return [("a", "1"), ("a", "2"), ("b", "3")]


class TestCallbackRequest:
    def test_parses_json_and_form_bodies_with_the_documented_precedence(self) -> None:
        json = CallbackRequest(
            body=b'{"amount": 1000.50, "count": 7, "nested": {"a": "b"}}', query={"q": "1"}
        )
        assert lossless_body(json)["amount"] == JsonNumber("1000.50")
        assert json.parsed_body() == {
            "amount": Decimal("1000.50"),
            "count": 7,
            "nested": {"a": "b"},
        }
        assert json.input()["q"] == "1"

        form = CallbackRequest(
            body="decision=ACCEPT&amount=10.50&decision=SECOND&note=a+b",
            query="?decision=QUERY&extra=x",
        )
        assert form.input() == {
            "decision": "ACCEPT",
            "amount": "10.50",
            "note": "a b",
            "extra": "x",
        }
        assert form.query_input() == {
            "decision": "QUERY",
            "amount": "10.50",
            "note": "a b",
            "extra": "x",
        }

    def test_reads_a_json_array_body_as_a_form_like_the_other_sdks(self) -> None:
        assert CallbackRequest(body="[1,2]").parsed_body() == {"[1,2]": ""}

    def test_decodes_empty_bodies_and_bodies_given_as_bytes(self) -> None:
        assert CallbackRequest(body="  \n﻿").parsed_body() == {}
        assert CallbackRequest().parsed_body() == {}
        assert CallbackRequest().body == ""
        assert CallbackRequest().raw_body == b""
        assert CallbackRequest(body='{"a":"ü"}'.encode()).parsed_body() == {"a": "ü"}
        assert CallbackRequest(body=bytearray(b"a=1")).body == "a=1"
        assert CallbackRequest(body=memoryview(b"a=1")).parsed_body() == {"a": "1"}
        assert CallbackRequest(body=b"\xff").body == "�"

    def test_accepts_headers_and_queries_in_every_shape(self) -> None:
        request = CallbackRequest(
            headers={"X-Signature": "sig", "X-Multi": ["a", "b"], "X-None": None},
            query={"orderId": "ORDER_1", "list": ["first", "second"], "none": None, "e": []},
        )
        assert request.header("x-signature") == "sig"
        assert request.header("X-SIGNATURE") == "sig"
        assert request.header("x-multi") == "a, b"
        assert request.header("authorization") is None
        assert request.query == {"orderId": "ORDER_1", "list": "first"}

        pairs = CallbackRequest(headers=[("X-A", "1"), ("x-a", "2")], query=[("a", "1")])
        assert pairs.headers == {"x-a": "1, 2"}
        assert pairs.query == {"a": "1"}
        assert CallbackRequest(query=b"a=1&a=2&b=").query == {"a": "1", "b": ""}
        django = CallbackRequest(query=QueryDict({"a": ["1", "2"], "b": []}))  # type: ignore[arg-type]
        assert django.query == {"a": "1"}
        assert CallbackRequest(query=MultiItems()).query == {"a": "1", "b": "3"}  # type: ignore[arg-type]

    def test_works_with_httpx_headers_and_query_params(self) -> None:
        headers = httpx.Headers([("X-A", "1"), ("X-A", "2")])
        request = CallbackRequest(headers=headers.multi_items(), query=httpx.QueryParams("a=1"))
        assert request.header("x-a") == "1, 2"
        assert request.query == {"a": "1"}

    def test_builds_a_json_request_from_a_decoded_payload(self) -> None:
        request = CallbackRequest.from_json(
            {"orderId": "ORDER_1", "amount": 1000, "exact": Decimal("10.50")},
            {"X-Signature": "sig", "Content-Type": "text/plain"},
        )
        assert request.body == '{"orderId":"ORDER_1","amount":1000,"exact":"10.50"}'
        assert request.header("Content-Type") == "application/json"
        assert request.header("x-signature") == "sig"
        assert request.input() == {"orderId": "ORDER_1", "amount": 1000, "exact": "10.50"}
        assert CallbackRequest.from_json({}).header("content-type") == "application/json"
        assert "CallbackRequest(body='{}'" in repr(CallbackRequest.from_json({}))


class TestResults:
    def test_describes_a_redirect_payment(self) -> None:
        payment = RedirectPayment(order_id="O1", url="https://pay.test")
        assert payment.flow is PaymentFlow.REDIRECT
        assert payment.gateway_reference is None
        assert payment.raw == {}

    def test_renders_a_form_payment_as_an_escaped_auto_submitting_page(self) -> None:
        payment = FormPayment(
            order_id="O1",
            action='https://pay.test/?a="b"&c=<d>',
            fields=(FormField("x", '"><script>alert(1)</script>'), FormField("o'k", "1")),
            enctype="multipart/form-data",
        )
        html = payment.to_html()
        assert "<script>alert(1)" not in html
        assert 'enctype="multipart/form-data"' in html
        assert "&#34;&gt;&lt;script&gt;" in html
        assert 'action="https://pay.test/?a=&#34;b&#34;&amp;c=&lt;d&gt;"' in html
        assert 'name="o&#39;k"' in html
        assert html.startswith("<!DOCTYPE html>")
        assert payment.flow is PaymentFlow.FORM
        assert payment.field("o'k") == "1"
        assert payment.field("missing") is None
        assert payment.values() == {"x": '"><script>alert(1)</script>', "o'k": "1"}
        default = FormPayment(order_id="O1", action="https://pay.test", fields=())
        assert default.enctype == "application/x-www-form-urlencoded"

    def test_builds_qr_data_uris_only_with_an_image(self) -> None:
        assert QrPayment(order_id="O1", qr_image="abc").qr_image_data_uri() == (
            "data:image/png;base64,abc"
        )
        kbz = QrPayment(order_id="O1", qr_string="kbzpay://qr")
        assert kbz.qr_image_data_uri() is None
        assert kbz.flow is PaymentFlow.QR

    def test_serializes_an_app_payment_without_its_raw_response(self) -> None:
        payment = AppPayment(
            order_id="O1", order_info="a=1", sign="S", sign_type="SHA256", raw={"x": 1}
        )
        assert payment.to_dict() == {
            "orderId": "O1",
            "orderInfo": "a=1",
            "sign": "S",
            "signType": "SHA256",
        }
        assert payment.flow is PaymentFlow.APP

    def test_builds_callbacks_and_status_results_for_your_own_tests(self) -> None:
        callback = PaymentCallback(
            order_id="O1", status=PaymentStatus.SUCCESSFUL, gateway_status="PAID"
        )
        assert callback.is_successful()
        assert callback.acknowledgement() == Acknowledgement.default()
        assert callback.raw == {}
        assert "PaymentCallback(order_id='O1', status='successful'" in repr(callback)
        pending = PaymentCallback(order_id="O1", status="pending", gateway_status="X")  # type: ignore[arg-type]
        assert pending.status is PaymentStatus.PENDING
        assert not pending.is_successful()

        result = PaymentStatusResult(status="failed", gateway_status="FAILED")  # type: ignore[arg-type]
        assert result.status is PaymentStatus.FAILED
        assert not result.is_successful()
        assert result.order_id is None
        assert result.raw == {}
        assert PaymentStatusResult(
            status=PaymentStatus.SUCCESSFUL, gateway_status="OK"
        ).is_successful()

    def test_acknowledges_with_an_empty_text_response_by_default(self) -> None:
        ack = Acknowledgement()
        assert (ack.status, ack.body, dict(ack.headers)) == (
            200,
            "",
            {"Content-Type": "text/plain"},
        )
        custom = Acknowledgement(status=202, body="ok", headers={"X-A": "1"})
        assert custom.headers == {"X-A": "1"}


class TestPaymentStatus:
    def test_lists_the_statuses_and_knows_which_are_final(self) -> None:
        assert [status.value for status in PaymentStatus] == [
            "successful",
            "pending",
            "failed",
            "canceled",
            "expired",
            "unknown",
        ]
        assert [status.value for status in PaymentStatus if status.is_final()] == [
            "successful",
            "failed",
            "canceled",
            "expired",
        ]
        assert PaymentStatus.CANCELED == "canceled"  # type: ignore[comparison-overlap]
        assert str(PaymentStatus.CANCELED) == "canceled"
        assert f"{PaymentStatus.EXPIRED:>8}" == " expired"
        assert [flow.value for flow in PaymentFlow] == ["redirect", "form", "qr", "app"]

    def test_resolves_gateway_statuses(self) -> None:
        statuses = {"OK": PaymentStatus.SUCCESSFUL}
        assert resolve_status(statuses, " OK ") is PaymentStatus.SUCCESSFUL
        assert resolve_status(statuses, "NOPE") is PaymentStatus.UNKNOWN
        assert resolve_status(statuses, None) is PaymentStatus.UNKNOWN


class TestErrors:
    def test_every_error_is_a_payment_error(self) -> None:
        invalid = InvalidPaymentDataError({"b": "B is bad.", "a": "A is bad."})
        assert str(invalid) == "Invalid payment data: A is bad. B is bad."
        assert dict(invalid.errors) == {"b": "B is bad.", "a": "A is bad."}
        api = ApiError("failed")
        assert (api.gateway_code, api.gateway_message, api.http_status, api.raw) == (
            None,
            None,
            0,
            {},
        )
        assert SignatureVerificationError("bad").raw == {}
        for error in (invalid, api, SignatureVerificationError("x"), ConfigurationError("g", "k")):
            assert isinstance(error, PaymentError)


class TestInternals:
    def test_parses_json_without_floats(self) -> None:
        parsed = parse_object('{"a": 1e3, "b": -0, "c": [1.0, true, null], "d": "x"}')
        assert parsed is not None
        assert to_plain(parsed) == {
            "a": Decimal("1e3"),
            "b": 0,
            "c": [Decimal("1.0"), True, None],
            "d": "x",
        }
        assert scalar_string(parsed["a"]) == "1e3"
        assert repr(JsonNumber("1")) == "JsonNumber('1')"
        assert JsonNumber("1") != "1"
        assert hash(JsonNumber("1")) == hash("1")
        assert parse_object('{"a": NaN}') is None
        assert parse_object("[1]") is None
        assert parse_object("{") is None
        assert parse_object("[" * 100000) is None
        digits = "9" * 5000
        assert to_plain(JsonNumber(digits)) == Decimal(digits)

    def test_reads_scalars_as_signed_text(self) -> None:
        assert scalar_string(True) == "true"
        assert scalar_string(5) == "5"
        assert scalar_string(1.5) == "1.5"
        assert scalar_string(float("nan")) is None
        assert scalar_string(Decimal("1.50")) == "1.50"
        assert scalar_string(Decimal("NaN")) is None
        assert scalar_string([]) is None
        assert get({"a": None}, "a") == ""

    def test_escapes_like_go_query_escape(self) -> None:
        assert query_escape("a b&c=d~e*f'(g)!") == "a+b%26c%3Dd~e%2Af%27%28g%29%21"

    def test_decodes_only_strict_base64(self) -> None:
        assert decode_base64("YWI=") == "ab"
        for value in ("", "YWI", "YW I=", "YW==I", "A==="):
            assert decode_base64(value) is None

    @pytest.mark.parametrize(
        ("value", "sandbox"),
        [
            (None, True),
            ("", True),
            ("true", True),
            ("garbage", True),
            ("false", False),
            (" FALSE ", False),
            ("0", False),
            ("f", False),
            ("No", False),
            ("off", False),
        ],
    )
    def test_reads_the_sandbox_flag(self, value: str | None, sandbox: bool) -> None:
        env = {} if value is None else {"X_SANDBOX": value}
        assert env_sandbox(env, "X_SANDBOX") is sandbox

    def test_reads_integers(self) -> None:
        assert env_int({"N": " -5 "}, "N") == -5
        assert env_int({"N": "+5"}, "N") == 5
        assert env_int({"N": "5.0"}, "N") is None
        assert env_int({}, "N") is None

    def test_tells_the_time(self) -> None:
        assert abs(current_time() - time.time()) < 5

    def test_validator_reports_the_first_error_per_field(self) -> None:
        validator = Validator().required("a", "").required("a", None).length("b", "abc", 1, 2)
        assert validator.failed()
        with pytest.raises(InvalidPaymentDataError) as info:
            validator.validate()
        assert dict(info.value.errors) == {
            "a": "The a field is required.",
            "b": "The b field must be between 1 and 2 characters.",
        }
        Validator().length("x", "", 1, 2).length("x", 5, 1, 2).validate()
        assert not Validator().url("u", "http://shop.test:8080/x").failed()
        assert Validator().url("u", "http://shop.test:99999").failed()
        assert Validator().url("u", "http://[::1").failed()

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            (1.5, "must not be a float"),
            (b"1", "must be an Amount"),
            ("-5", "non-negative number"),
            (-5, "non-negative number"),
            ("1000.505", "accepts at most 2 decimal places"),
            ("12345", "must not be greater than 4 characters"),
        ],
    )
    def test_validates_amounts(self, value: Any, message: str) -> None:
        rule = AmountRule("Gateway", 2, max_length=4)
        with pytest.raises(InvalidPaymentDataError) as info:
            Validator().amount("amount", value, rule).validate()
        assert message in info.value.errors["amount"]


class TestTokenCache:
    def test_stores_expires_and_deletes_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: now[0])
        cache = MemoryTokenCache()
        assert isinstance(cache, TokenCache)
        cache.set("a", "1", 10)
        cache.set("forever", "2", 0)
        assert cache.get("a") == "1"
        now[0] += 10
        assert cache.get("a") is None
        assert cache.get("missing") is None
        assert cache.get("forever") == "2"
        cache.delete("forever")
        cache.delete("forever")
        assert cache.get("forever") is None
        cache.set("b", "3", 5)
        cache.clear()
        assert cache.get("b") is None

    def test_recognizes_async_caches(self) -> None:
        class Async:
            async def get(self, key: str) -> str | None:
                return None

            async def set(self, key: str, value: str, ttl_seconds: int) -> None:
                return None

            async def delete(self, key: str) -> None:
                return None

        assert isinstance(Async(), AsyncTokenCache)


class TestHttpClients:
    def test_creates_and_closes_its_own_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"Response": {"result": "SUCCESS", "code": "0"}})

        created: list[httpx.Client] = []
        original = httpx.Client

        def client(**options: Any) -> httpx.Client:
            assert options == {"timeout": 5.0}
            instance = original(transport=httpx.MockTransport(handler))
            created.append(instance)
            return instance

        monkeypatch.setattr(httpx, "Client", client)
        with KbzPay(KBZ, timeout=5.0) as kbz:
            assert kbz.status("O1").status is PaymentStatus.UNKNOWN
            kbz.status("O1")
        assert len(created) == 1
        assert created[0].is_closed
        kbz.close()

    def test_keeps_a_client_you_passed_open(self) -> None:
        client = httpx.Client()
        KbzPay(KBZ, http_client=client).close()
        assert not client.is_closed
        client.close()

    def test_creates_and_closes_its_own_async_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"Response": {"result": "SUCCESS", "code": "0"}})

        created: list[httpx.AsyncClient] = []
        original = httpx.AsyncClient

        def client(**options: Any) -> httpx.AsyncClient:
            assert options == {"timeout": 30.0}
            instance = original(transport=httpx.MockTransport(handler))
            created.append(instance)
            return instance

        monkeypatch.setattr(httpx, "AsyncClient", client)

        async def scenario() -> None:
            async with AsyncKbzPay(KBZ) as kbz:
                await kbz.status("O1")
                await kbz.status("O1")
            await kbz.aclose()

        asyncio.run(scenario())
        assert len(created) == 1
        assert created[0].is_closed

    def test_keeps_an_async_client_you_passed_open(self) -> None:
        client = httpx.AsyncClient()
        asyncio.run(AsyncKbzPay(KBZ, http_client=client).aclose())
        assert not client.is_closed

    def test_wraps_timeouts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("")

        kbz = KbzPay(KBZ, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with pytest.raises(
            ApiError, match=r"Could not reach https://kbz.test/queryorder: ReadTimeout"
        ):
            kbz.status("O1")


def test_exposes_the_version() -> None:
    assert __version__ == "4.0.0a1"
