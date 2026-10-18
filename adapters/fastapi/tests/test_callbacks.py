from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request, Response
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

from fastapi_myanmar_payments import (
    AyaPayDep,
    CallbackRequestDep,
    CyberSourceDep,
    KbzPayDep,
    WavePayDep,
    YomaMmqrDep,
    acknowledgement_response,
    callback_request,
    payments_lifespan,
    verify_callback,
)
from tests.helpers import CONFIG, fixture, hmac_base64, hmac_hex, payments, serve

pytestmark = pytest.mark.anyio

received: list[PaymentCallback] = []

app = FastAPI(lifespan=payments_lifespan(payments()))


@app.post("/echo")
async def echo(request: CallbackRequestDep) -> dict[str, Any]:
    return {
        "body": base64.b64encode(request.raw_body).decode(),
        "headers": dict(request.headers),
        "query": dict(request.query),
    }


def acknowledge(callback: PaymentCallback) -> Response:
    received.append(callback)
    return acknowledgement_response(callback.acknowledgement)


@app.post("/kbz")
async def kbz_callback(kbz: KbzPayDep, request: CallbackRequestDep) -> Response:
    return acknowledge(verify_callback(kbz, request))


@app.post("/wave")
async def wave_callback(wave: WavePayDep, request: CallbackRequestDep) -> Response:
    return acknowledge(verify_callback(wave, request))


@app.post("/aya")
async def aya_callback(aya: AyaPayDep, request: CallbackRequestDep) -> Response:
    return acknowledge(verify_callback(aya, request))


@app.post("/yoma")
async def yoma_callback(yoma: YomaMmqrDep, request: CallbackRequestDep) -> Response:
    return acknowledge(verify_callback(yoma, request))


@app.post("/cyber-source")
async def cyber_source_callback(cyber: CyberSourceDep, request: CallbackRequestDep) -> Response:
    return acknowledge(verify_callback(cyber, request))


sync_kbz = KbzPay(KbzPayConfig(**CONFIG["kbz_pay"]))


@app.post("/sync-kbz")
def sync_kbz_callback(request: CallbackRequestDep) -> Response:
    return acknowledge(verify_callback(sync_kbz, request))


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    received.clear()
    async with serve(app) as test_client:
        yield test_client


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


def wave_payload() -> dict[str, Any]:
    vector = fixture("wave_pay/callback.json")
    return {**vector["payload"], "hashValue": hmac_hex(vector["hash_string"], "test-secret")}


def aya_input() -> dict[str, str]:
    vector = fixture("aya_pay/callback_payload.json")
    payload = base64.b64encode(json.dumps(vector["payload"]).encode()).decode()
    return {"payload": payload, "checkSum": hmac_hex(vector["checksum_string"], "test-secret")}


def yoma_payload(status: str = "SUCCESS") -> dict[str, str]:
    digest = hmac_hex(f"orderNumber=ORDER_1&status={status}", "ORDER_1hash-key")
    return {"orderNumber": "ORDER_1", "status": status, "hashValue": digest}


def cyber_source_form() -> dict[str, str]:
    fields = {
        "decision": "ACCEPT",
        "req_reference_number": "ORDER_1",
        "req_amount": "1000.00",
        "signed_field_names": "decision,req_reference_number,req_amount,signed_field_names",
    }
    signed = ",".join(f"{name}={value}" for name, value in fields.items())
    return {**fields, "signature": hmac_base64(signed, "cyber-secret")}


class TestCallbackRequest:
    async def test_keeps_the_raw_body_headers_and_query_string(
        self, client: httpx.AsyncClient
    ) -> None:
        body = b'{ "amount" : 1000.50 ,"a":"\xe1\x80\x80"}\n'
        response = await client.post(
            "/echo?b=2&a=1&a=3",
            content=body,
            headers=[("X-Webhook-Secret", "s"), ("X-Many", "1"), ("X-Many", "2")],
        )
        echoed = response.json()
        assert base64.b64decode(echoed["body"]) == body
        assert echoed["headers"]["x-webhook-secret"] == "s"
        assert echoed["headers"]["x-many"] == "1, 2"
        assert echoed["query"] == {"b": "2", "a": "1"}

    async def test_can_be_awaited_outside_dependency_injection(self) -> None:
        sent = [{"type": "http.request", "body": b"a=1", "more_body": False}]

        async def receive() -> dict[str, Any]:
            return sent.pop()

        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
            "query_string": b"",
        }
        request = await callback_request(Request(scope, receive))
        assert request.raw_body == b"a=1"
        assert request.parsed_body() == {"a": "1"}


class TestVerifiedCallbacks:
    async def test_kbz_pay_json_with_exact_numbers(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/kbz", content=kbz_body(), headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200
        assert response.content == b"success"
        assert response.headers["content-type"] == "text/plain"
        [callback] = received
        assert callback.order_id == "ORDER_1"
        assert callback.amount == "1000.50"
        assert callback.status is PaymentStatus.SUCCESSFUL

    async def test_wave_pay_shared_vector(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/wave", json=wave_payload())
        assert response.status_code == 200
        assert response.content == b""
        assert response.headers["content-type"] == "text/plain"
        assert received[0].order_id == "100"
        assert received[0].amount == "1000"

    async def test_aya_pay_json_body(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/aya", json=aya_input())
        assert response.status_code == 200
        assert received[0].order_id == "ORD123456"
        assert received[0].is_successful()

    async def test_aya_pay_query_string(self, client: httpx.AsyncClient) -> None:
        assert (await client.post(f"/aya?{urlencode(aya_input())}")).status_code == 200
        assert received[0].order_id == "ORD123456"

    async def test_yoma_mmqr_needs_its_webhook_secret_header(
        self, client: httpx.AsyncClient
    ) -> None:
        headers = {"X-Webhook-Secret": "webhook-secret"}
        response = await client.post("/yoma", json=yoma_payload(), headers=headers)
        assert response.status_code == 200
        assert received[0].amount is None
        assert (await client.post("/yoma", json=yoma_payload())).status_code == 400
        assert len(received) == 1

    async def test_cyber_source_form_post(self, client: httpx.AsyncClient) -> None:
        form = {**cyber_source_form(), "unsigned": "dropped"}
        response = await client.post("/cyber-source", data=form)
        assert response.status_code == 200
        assert received[0].order_id == "ORDER_1"
        assert received[0].amount == "1000.00"
        assert "unsigned" not in received[0].raw

    async def test_a_sync_gateway_in_a_sync_endpoint(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/sync-kbz", content=kbz_body())
        assert response.content == b"success"
        assert received[0].amount == "1000.50"


class TestRejectedCallbacks:
    @pytest.mark.parametrize(
        ("path", "body", "content_type"),
        [
            ("/kbz", kbz_body().replace(b"PAY_SUCCESS", b"PAY_FAILED"), "application/json"),
            ("/kbz", kbz_body().replace(b"1000.50", b"1000.5"), "application/json"),
            ("/kbz", b'{"Request": {', "application/json"),
            ("/kbz", b"", "application/json"),
            ("/wave", json.dumps({**wave_payload(), "amount": "1"}).encode(), "application/json"),
            ("/wave", b"\xff\xfe\x00garbage", "application/json"),
            ("/aya", urlencode({**aya_input(), "checkSum": "0" * 64}).encode(), "text/plain"),
            ("/aya", b"payload=%%%&&checkSum", "application/x-www-form-urlencoded"),
            ("/cyber-source", b"decision=ACCEPT&signature=", "application/x-www-form-urlencoded"),
            (
                "/cyber-source",
                urlencode({**cyber_source_form(), "decision": "DECLINE"}).encode(),
                "application/x-www-form-urlencoded",
            ),
        ],
    )
    async def test_answers_400_without_leaking_anything(
        self, client: httpx.AsyncClient, path: str, body: bytes, content_type: str
    ) -> None:
        response = await client.post(path, content=body, headers={"Content-Type": content_type})
        assert response.status_code == 400
        assert response.json() == {"detail": "Callback verification failed."}
        assert received == []

    def test_chains_the_core_error_and_lets_other_errors_through(self) -> None:
        with pytest.raises(HTTPException) as info:
            verify_callback(sync_kbz, CallbackRequest(body=b"{}"))
        assert info.value.status_code == 400
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
        assert response.body == b'{"ok":true}'
        assert response.headers["content-type"] == "application/json"
        assert response.headers["x-gateway"] == "ack"

    def test_default_acknowledgement_is_an_empty_plain_text_200(self) -> None:
        response = acknowledgement_response(Acknowledgement.default())
        assert response.status_code == 200
        assert response.body == b""
        assert response.headers["content-type"] == "text/plain"
        assert response.headers["content-length"] == "0"
