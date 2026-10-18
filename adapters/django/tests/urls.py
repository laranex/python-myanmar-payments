from __future__ import annotations

import base64
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from python_myanmar_payments import PaymentCallback, WavePayItem, WavePayPaymentData

from django_myanmar_payments import (
    acknowledgement_response,
    callback_request,
    get_async_payments,
    get_payments,
    verify_callback,
)

received: list[PaymentCallback] = []


def wave_data() -> WavePayPaymentData:
    return WavePayPaymentData(
        order_id="100",
        callback_url="https://shop.test/wave/callback",
        return_url="https://shop.test/done",
        description="Order 100",
        items=[WavePayItem("Shoes", 1000)],
    )


@csrf_exempt
@require_POST
def callback(request: HttpRequest, gateway: str) -> HttpResponse:
    callback = verify_callback(getattr(get_payments(), gateway)(), request)
    received.append(callback)
    return acknowledgement_response(callback.acknowledgement)


@csrf_exempt
@require_POST
async def async_callback(request: HttpRequest, gateway: str) -> HttpResponse:
    callback = verify_callback(getattr(get_async_payments(), gateway)(), request)
    received.append(callback)
    return acknowledgement_response(callback.acknowledgement)


@csrf_exempt
def echo(request: HttpRequest) -> JsonResponse:
    callback = callback_request(request)
    data: dict[str, Any] = {
        "body": base64.b64encode(callback.raw_body).decode(),
        "headers": dict(callback.headers),
        "query": dict(callback.query),
    }
    return JsonResponse(data)


def pay(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"url": get_payments().wave_pay().initiate(wave_data()).url})


async def async_pay(request: HttpRequest) -> JsonResponse:
    payment = await get_async_payments().wave_pay().initiate(wave_data())
    return JsonResponse({"url": payment.url})


urlpatterns = [
    path("callback/<str:gateway>", callback),
    path("async-callback/<str:gateway>", async_callback),
    path("echo", echo),
    path("pay", pay),
    path("async-pay", async_pay),
]
