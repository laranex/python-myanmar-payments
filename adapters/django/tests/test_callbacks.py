from __future__ import annotations

import base64
import json
import logging
from collections.abc import Iterator
from urllib.parse import urlencode

import pytest
from django.core.exceptions import SuspiciousOperation
from django.test import AsyncClient, Client, override_settings
from python_myanmar_payments import (
    Acknowledgement,
    CallbackRequest,
    KbzPay,
    KbzPayConfig,
    KbzPaySigner,
    PaymentCallback,
    PaymentStatus,
    SignatureVerificationError,
)

from django_myanmar_payments import (
    CallbackVerificationFailed,
    acknowledgement_response,
    verify_callback,
)
from tests.helpers import CONFIG, HTTP_CLIENTS, fixture, hmac_base64, hmac_hex
from tests.urls import received

pytestmark = pytest.mark.anyio

JSON = "application/json"
FORM = "application/x-www-form-urlencoded"


@pytest.fixture(autouse=True)
def clear_received() -> Iterator[None]:
    received.clear()
    yield
    received.clear()


def kbz_body() -> bytes:
    """A KBZ callback with JSON numbers: re-encoding them would break the signature."""
    fields = {
        "merch_order_id": "ORDER_1",
        "mm_order_id": "0112345",
        "total_amount": "1000.50",
        "trade_status": "PAY_SUCCESS",
        "notify_time": "1536637503",
    }
    sign = KbzPaySigner(CONFIG["kbz_pay"]["app_key"]).sign(fields)
    return (
        '{"Request":{"merch_order_id":"ORDER_1","mm_order_id":"0112345",'
        '"total_amount":1000.50,"trade_status":"PAY_SUCCESS","notify_time":1536637503,'
        f'"sign_type":"SHA256","sign":"{sign}"}}}}'
    ).encode()


def wave_body() -> bytes:
    vector = fixture("wave_pay/callback.json")
    payload = {**vector["payload"], "hashValue": hmac_hex(vector["hash_string"], "test-secret")}
    return json.dumps(payload).encode()


def aya_input() -> dict[str, str]:
    vector = fixture("aya_pay/callback_payload.json")
    payload = base64.b64encode(json.dumps(vector["payload"]).encode()).decode()
    return {"payload": payload, "checkSum": hmac_hex(vector["checksum_string"], "test-secret")}


def yoma_body() -> bytes:
    digest = hmac_hex("orderNumber=ORDER_1&status=SUCCESS", "ORDER_1hash-key")
    return json.dumps({"orderNumber": "ORDER_1", "status": "SUCCESS", "hashValue": digest}).encode()


def cyber_source_form() -> dict[str, str]:
    fields = {
        "decision": "ACCEPT",
        "req_reference_number": "ORDER_1",
        "req_amount": "1000.00",
        "signed_field_names": "decision,req_reference_number,req_amount,signed_field_names",
    }
    signed = ",".join(f"{name}={value}" for name, value in fields.items())
    return {**fields, "signature": hmac_base64(signed, "cyber-secret")}


VALID = [
    ("kbz_pay", "", kbz_body(), JSON, {}, "ORDER_1", b"success"),
    ("wave_pay", "", wave_body(), JSON, {}, "100", b""),
    ("aya_pay", "", json.dumps(aya_input()).encode(), JSON, {}, "ORD123456", b""),
    ("aya_pay", f"?{urlencode(aya_input())}", b"", FORM, {}, "ORD123456", b""),
    (
        "yoma_mmqr",
        "",
        yoma_body(),
        JSON,
        {"X-Webhook-Secret": "webhook-secret"},
        "ORDER_1",
        b"",
    ),
    ("cyber_source", "", urlencode(cyber_source_form()).encode(), FORM, {}, "ORDER_1", b""),
]

INVALID = [
    ("kbz_pay", kbz_body().replace(b"PAY_SUCCESS", b"PAY_FAILED"), JSON, {}),
    ("kbz_pay", kbz_body().replace(b"1000.50", b"1000.5"), JSON, {}),
    ("kbz_pay", b'{"Request": {', JSON, {}),
    ("kbz_pay", b"", JSON, {}),
    ("wave_pay", wave_body().replace(b'"1000"', b'"1"'), JSON, {}),
    ("wave_pay", b"\xff\xfe\x00garbage", JSON, {}),
    ("aya_pay", urlencode({**aya_input(), "checkSum": "0" * 64}).encode(), FORM, {}),
    ("aya_pay", b"payload=%%%&&checkSum", FORM, {}),
    ("yoma_mmqr", yoma_body(), JSON, {}),
    ("yoma_mmqr", yoma_body(), JSON, {"X-Webhook-Secret": "wrong"}),
    ("cyber_source", b"decision=ACCEPT&signature=", FORM, {}),
    ("cyber_source", urlencode({**cyber_source_form(), "decision": "DECLINE"}).encode(), FORM, {}),
]


class TestCallbackRequest:
    def test_keeps_the_raw_body_headers_and_query_string(self) -> None:
        body = b'{ "amount" : 1000.50 ,"a":"\xe1\x80\x80"}\n'
        response = Client().post(
            "/echo?b=2&a=1&a=3",
            data=body,
            content_type=JSON,
            headers={"X-Webhook-Secret": "s"},
        )
        echoed = response.json()
        assert base64.b64decode(echoed["body"]) == body
        assert echoed["headers"]["x-webhook-secret"] == "s"
        assert echoed["headers"]["content-type"] == JSON
        assert echoed["query"] == {"b": "2", "a": "1"}

    async def test_works_in_async_views(self) -> None:
        response = await AsyncClient().post(
            "/async-callback/kbz_pay", data=kbz_body(), content_type=JSON
        )
        assert response.content == b"success"


class TestVerifiedCallbacks:
    @pytest.mark.parametrize(
        ("gateway", "query", "body", "content_type", "headers", "order_id", "ack"), VALID
    )
    def test_sync_views(
        self,
        gateway: str,
        query: str,
        body: bytes,
        content_type: str,
        headers: dict[str, str],
        order_id: str,
        ack: bytes,
    ) -> None:
        response = Client(enforce_csrf_checks=True).post(
            f"/callback/{gateway}{query}", data=body, content_type=content_type, headers=headers
        )
        assert response.status_code == 200
        assert response.content == ack
        assert response["Content-Type"] == "text/plain"
        [callback] = received
        assert callback.order_id == order_id
        assert callback.status is PaymentStatus.SUCCESSFUL

    @pytest.mark.parametrize(
        ("gateway", "query", "body", "content_type", "headers", "order_id", "ack"), VALID
    )
    async def test_async_views(
        self,
        gateway: str,
        query: str,
        body: bytes,
        content_type: str,
        headers: dict[str, str],
        order_id: str,
        ack: bytes,
    ) -> None:
        response = await AsyncClient().post(
            f"/async-callback/{gateway}{query}",
            data=body,
            content_type=content_type,
            headers=headers,
        )
        assert response.status_code == 200
        assert response.content == ack
        assert response["Content-Type"] == "text/plain"
        assert received[0].order_id == order_id

    def test_keeps_exact_amounts_and_signed_fields_only(self) -> None:
        Client().post("/callback/kbz_pay", data=kbz_body(), content_type=JSON)
        form = urlencode({**cyber_source_form(), "unsigned": "dropped"}).encode()
        Client().post("/callback/cyber_source", data=form, content_type=FORM)
        kbz, cyber = received
        assert kbz.amount == "1000.50"
        assert cyber.amount == "1000.00"
        assert "unsigned" not in cyber.raw

    def test_an_async_view_under_wsgi(self) -> None:
        response = Client().post("/async-callback/kbz_pay", data=kbz_body(), content_type=JSON)
        assert response.content == b"success"


class TestRejectedCallbacks:
    @pytest.mark.parametrize(("gateway", "body", "content_type", "headers"), INVALID)
    def test_answers_400_logs_and_leaks_nothing(
        self,
        caplog: pytest.LogCaptureFixture,
        gateway: str,
        body: bytes,
        content_type: str,
        headers: dict[str, str],
    ) -> None:
        with caplog.at_level(logging.ERROR, "django.security"):
            response = Client().post(
                f"/callback/{gateway}", data=body, content_type=content_type, headers=headers
            )
        assert response.status_code == 400
        assert b"ORDER" not in response.content
        assert received == []
        [record] = caplog.records
        assert record.name == "django.security.CallbackVerificationFailed"
        assert record.getMessage() == "Callback verification failed."

    @pytest.mark.parametrize(("gateway", "body", "content_type", "headers"), INVALID[:2])
    async def test_async_views_answer_400(
        self, gateway: str, body: bytes, content_type: str, headers: dict[str, str]
    ) -> None:
        response = await AsyncClient().post(
            f"/async-callback/{gateway}", data=body, content_type=content_type, headers=headers
        )
        assert response.status_code == 400
        assert received == []

    def test_chains_the_core_error_and_lets_other_errors_through(self) -> None:
        kbz = KbzPay(KbzPayConfig(**CONFIG["kbz_pay"]))
        with pytest.raises(CallbackVerificationFailed) as info:
            verify_callback(kbz, CallbackRequest(body=b"{}"))
        assert isinstance(info.value, SuspiciousOperation)
        assert isinstance(info.value.__cause__, SignatureVerificationError)

        class Broken:
            def handle_callback(self, request: CallbackRequest) -> PaymentCallback:
                raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            verify_callback(Broken(), CallbackRequest())


class TestAcknowledgementResponse:
    def test_keeps_status_body_and_headers(self) -> None:
        response = acknowledgement_response(
            Acknowledgement(
                status=202,
                body='{"ok":true}',
                headers={"Content-Type": "application/json", "X-Gateway": "ack"},
            )
        )
        assert response.status_code == 202
        assert response.content == b'{"ok":true}'
        assert response["Content-Type"] == "application/json"
        assert response["X-Gateway"] == "ack"

    def test_default_acknowledgement_is_an_empty_plain_text_200(self) -> None:
        response = acknowledgement_response(Acknowledgement.default())
        assert response.status_code == 200
        assert response.content == b""
        assert response["Content-Type"] == "text/plain"


class TestPayments:
    def test_sync_and_async_views_initiate_payments(self) -> None:
        url = "https://payments.wavemoney.io/authenticate?transaction_id=T1"
        with override_settings(MYANMAR_PAYMENTS={**CONFIG, **HTTP_CLIENTS}):
            assert Client().get("/pay").json() == {"url": url}
            assert Client().get("/async-pay").json() == {"url": url}
