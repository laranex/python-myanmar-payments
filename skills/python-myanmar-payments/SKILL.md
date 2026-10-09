---
name: python-myanmar-payments
description: >
  Integrate Myanmar payment gateways (KBZ Pay, Wave Money, AYA Pay, Yoma MMQR, CyberSource) in a Python app (Django, Flask, FastAPI or plain Python) with python-myanmar-payments.
license: MIT
metadata:
  author: Nay Thu Khant
---

# Python Myanmar Payments

## When to use

Use this skill when a Python app takes payments through KBZ Pay, Wave Money, AYA Payment Gateway, Yoma MMQR or CyberSource. Start payments and verify callbacks with the package's typed API; never build gateway signatures by hand.

## Install

```bash
pip install python-myanmar-payments
```

Requires Python 3.10+. Its only dependency is `httpx`. Import everything from `python_myanmar_payments`. Every gateway that calls an API has a sync class (`KbzPay`, `WaveMoney`, `AyaPay`, `YomaMmqr`) and an async twin (`AsyncKbzPay`, `AsyncWaveMoney`, `AsyncAyaPay`, `AsyncYomaMmqr`) with the same methods, awaited. `CyberSource` makes no network calls and serves both.

## Configure

Every gateway has a config class with `from_env()` (default `os.environ`), reading `KBZ_PAY_*`, `WAVE_MONEY_*`, `AYA_PAY_*` (or `AYA_PGW_*`), `YOMA_MMQR_*` and `CYBER_SOURCE_*` (the same variables as the PHP, Laravel, Go and Node packages). `sandbox` defaults to `True`; set `*_SANDBOX=false` (or `sandbox=False`) in production.

```python
from python_myanmar_payments import MyanmarPayments

payments = MyanmarPayments.from_env()  # create once, share across requests
kbz = payments.kbz_pay()  # also wave_money(), aya_pay(), yoma_mmqr(), cyber_source()
```

- In async code use `AsyncMyanmarPayments.from_env()`; its `kbz_pay()` returns an `AsyncKbzPay`.
- Or build one gateway: `KbzPay(KbzPayConfig(app_id=..., app_key=..., merchant_code=...))` or `KbzPay.from_env()`.
- Options (keyword arguments): `http_client` (your own `httpx.Client` / `httpx.AsyncClient`) and `timeout` (seconds, default 30). Yoma and the facades also take `token_cache` (any `TokenCache`, default `MemoryTokenCache`; async code may pass an `AsyncTokenCache`; back it with Redis when you run several processes).
- Close the HTTP clients the package created with `payments.close()` / `await payments.aclose()` or a `with` / `async with` block.
- A missing credential raises `ConfigurationError` (`gateway`, `key`).

## Use

### Amounts

Amounts are `Amount.kyat(1000)`, `Amount.parse("1000.50")`, a whole `int`, decimal text such as `"1000.50"` or a `Decimal`; floats like `10.5` are rejected. Only KBZ Pay (up to 2 decimals) and CyberSource accept decimals; Wave, AYA and Yoma take whole kyat. Invalid data raises `InvalidPaymentDataError` with `errors` per field (`order_id`, `amount`, ...), before any request is sent.

### Start a payment

```python
from python_myanmar_payments import Amount, KbzPayPaymentData

payment = kbz.pwa(
    KbzPayPaymentData(
        order_id="ORDER_1",
        amount=Amount.kyat(1000),
        callback_url="https://shop.test/payments/kbz/callback",
    )
)
return redirect(payment.url)
```

- `RedirectPayment` (`url`) from `kbz.pwa(data)` and `wave.initiate(WaveMoneyPaymentData(...))` with `WaveMoneyItem` line items. Wave fills `data.merchant_reference_id` when empty; store it.
- `FormPayment` from `aya.initiate(AyaPayPaymentData(...))` and `cyber_source.initiate(CyberSourcePaymentData(...))` (no network call, never awaited): return `payment.to_html()` as an auto-submitting page, or render `action`, `fields` and `enctype` yourself.
- `QrPayment` from `kbz.qr(data)` (encode `qr_string`) and `yoma.initiate(YomaMmqrPaymentData(...))` (`qr_image_data_uri()`, `expires_at`, `reference`). A Yoma QR lives `YomaMmqr.QR_LIFETIME_SECONDS` (120); renew it with `yoma.renew_qr(order_id)`.
- `AppPayment` from `kbz.app(data)`: send `payment.to_dict()` (`orderId`, `orderInfo`, `sign`, `signType`) to your mobile app.

AYA needs a channel: `aya.services()` lists `AyaPayService` entries (`key`, `supports(method)`), then `aya.initiate(AyaPayPaymentData(order_id="ORDER123", amount=1000, channel="kbz_pay", method=AyaPayMethod.QR))`.

### Handle the callback

Build a `CallbackRequest` from the raw body bytes, the headers and the query string, never from re-serialized input:

```python
from python_myanmar_payments import CallbackRequest, SignatureVerificationError

request = CallbackRequest(
    body=django_request.body,  # Flask: request.get_data(); FastAPI: await request.body()
    headers=django_request.headers,
    query=django_request.META["QUERY_STRING"],  # Flask: request.query_string
)
try:
    callback = kbz.handle_callback(request)
except SignatureVerificationError:
    return HttpResponse("invalid signature", status=400)
if callback.is_successful():
    ...  # compare callback.amount with the order, then fulfill callback.order_id once
ack = callback.acknowledgement()
return HttpResponse(ack.body, status=ack.status, headers=ack.headers)
```

- `handle_callback()` and `verify_redirect()` are plain methods on the async classes too (no network call).
- Check AYA's browser return with `aya.verify_redirect(request)`.
- For production, store the verified call, acknowledge immediately, then process it once in the background.

### Check status and handle errors

- `kbz.status(order_id)`, `aya.status(order_id)` and `yoma.status(reference)` return `PaymentStatusResult` with `status` and `is_successful()`. Wave Money and CyberSource have no status API.
- Statuses: `PaymentStatus.SUCCESSFUL`, `PENDING`, `FAILED`, `CANCELED`, `EXPIRED`, `UNKNOWN` (string enum: `"successful"`, ...); `status.is_final()`.
- Gateway failures raise `ApiError` (`gateway_code`, `gateway_message`, `http_status`, `raw`; network errors are chained as `__cause__`). All errors extend `PaymentError`.

## Test your app

- Pass an `httpx.Client(transport=httpx.MockTransport(handler))` (or an `httpx.AsyncClient`) as `http_client`, or mock with `respx`.
- Replay a stored callback with `CallbackRequest.from_json(payload, headers)` or `CallbackRequest(body=..., headers=..., query=...)`; it is still signature-checked.
- To test your own fulfillment code, build `PaymentCallback(order_id="ORDER_1", status=PaymentStatus.SUCCESSFUL, gateway_status="PAY_SUCCESS")` yourself.

## Avoid

- Fulfilling orders from return pages or query strings; fulfill only from a verified callback or a status check.
- Treating `pending` or `unknown` as paid.
- Passing floats or `float(price)` as amounts; use `Amount.parse()` or a `Decimal`.
- Building the `CallbackRequest` from `request.json` / `request.POST` instead of the raw body.
- Calling the sync classes inside an event loop; use the `Async*` classes there.
- Reusing a Wave `merchant_reference_id`, or calling Yoma `initiate()` twice for one order (use `renew_qr()`).
- Opening a KBZ Pay PWA link outside a phone with the KBZ Pay app.
