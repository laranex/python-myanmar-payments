---
name: python-myanmar-payments
description: >
  Integrate Myanmar payment gateways (KBZ Pay, Wave Pay, AYA Pay, Yoma MMQR, CyberSource) in a Python app (Django, Flask, FastAPI or plain Python) with python-myanmar-payments.
license: MIT
metadata:
  author: Nay Thu Khant
---

# Python Myanmar Payments

## When to use

Use this skill when a Python app takes payments through KBZ Pay, Wave Pay, AYA Payment Gateway, Yoma MMQR or CyberSource. Start payments and verify callbacks with the package's typed API; never build gateway signatures by hand. It works the same in Django, Flask, FastAPI or plain Python.

## Install

```bash
pip install python-myanmar-payments
```

Requires Python 3.10+ and depends only on `httpx`. Import everything from `python_myanmar_payments`. Every gateway that calls an API has a sync class (`KbzPay`, `WavePay`, `AyaPay`, `YomaMmqr`) and an async twin (`AsyncKbzPay`, `AsyncWavePay`, `AsyncAyaPay`, `AsyncYomaMmqr`) with the same methods, awaited; use the async ones inside an event loop. `CyberSource` makes no network calls and serves both.

## Configure

`MyanmarPayments.from_env()` reads `KBZ_PAY_*`, `WAVE_PAY_*`, `AYA_PAY_*`, `YOMA_MMQR_*` and `CYBER_SOURCE_*` plus `MYANMAR_PAYMENTS_HTTP_TIMEOUT` from `os.environ` (the same variables as the PHP, Go and Node SDKs). Every setting of a gateway you use is required and has no default, except the HTTP timeout in seconds (`MYANMAR_PAYMENTS_HTTP_TIMEOUT`, for every gateway except CyberSource), which defaults to 30 (`DEFAULT_TIMEOUT_SECONDS`). That includes the URLs (production's or the gateway's UAT ones, with matching credentials; the config classes' `PRODUCTION_*` constants name production's), the time to live (`KBZ_PAY_TIME_TO_LIVE_IN_MINUTES`, 1 to 120; `WAVE_PAY_TIME_TO_LIVE_IN_SECONDS`, greater than 0; a payment's `time_to_live_minutes` / `time_to_live_seconds` overrides them), `YOMA_MMQR_WEBHOOK_SECRET` (every Yoma callback must carry it in `X-Webhook-Secret`), `WAVE_PAY_MERCHANT_NAME` and `YOMA_MMQR_API_VERSION` (e.g. `v1rc`).

```python
from python_myanmar_payments import MyanmarPayments

payments = MyanmarPayments.from_env()  # create once, share across requests
kbz = payments.kbz_pay()  # also wave_pay(), aya_pay(), yoma_mmqr(), cyber_source()
```

- Or build one gateway: `KbzPay(KbzPayConfig(app_id=..., app_key=..., merchant_code=..., api_url=KbzPayConfig.PRODUCTION_API_URL, pwa_url=KbzPayConfig.PRODUCTION_PWA_URL, time_to_live_minutes=15))` (add `timeout_seconds=` to change the 30-second default), `KbzPay.from_env()` or `KbzPay(KbzPayConfig.from_env())`; in async code use `AsyncMyanmarPayments.from_env()` and `AsyncKbzPay`.
- Or pass the settings directly: gateways and `MyanmarPayments(kbz_pay={"app_id": ..., "app_key": ..., "merchant_code": ..., "api_url": ..., "pwa_url": ..., "time_to_live_minutes": 15, "timeout_seconds": 30})` take config objects or mappings of their keyword arguments (other keys are ignored, so one shared mapping can configure several gateways; credentials may be text or integers, e.g. `"merchant_code": 200123`).
- Options (keyword arguments): `http_client` (your own `httpx.Client` / `httpx.AsyncClient`) (it keeps its own timeout; without one the gateway creates a client whose `timeout_seconds` is one overall deadline per request, connecting and reading included); close the clients the package created with `payments.close()` / `await payments.aclose()` or a `with` block. Yoma and the facades also take `token_cache` (any `TokenCache`, default `MemoryTokenCache`; `AsyncYomaMmqr`/`AsyncMyanmarPayments` also take an `AsyncTokenCache`; back it with Redis when you run several processes).
- A missing setting raises `ConfigurationError` (`gateway`, `key`), and a time setting that is not a whole number from 1 to 2147483647 raises it too (an unset or blank timeout falls back to 30 seconds; `"0"` or `"2.5"` is still an error). Settings are checked in a fixed order (credentials, URLs, time to live, then the timeout) and only ASCII whitespace is trimmed.

## Use

### Amounts

Amounts are `Amount.kyat(1000)`, `Amount.parse("1000.50")`, a whole `int`, decimal text or a `Decimal`; never a float such as `10.5`. Only KBZ Pay (up to 2 decimals) and CyberSource accept decimals; Wave, AYA and Yoma take whole kyat. Invalid data raises `InvalidPaymentDataError` with `errors` per field (`order_id`, `amount`, ...), before any request is sent; a negative amount is reported as `The amount field must not be negative.` with the other errors. Callback, return and cancel URLs must be plain ASCII `http://` or `https://` URLs with a valid host name or bracketed IP (no spaces, no `_` or non-ASCII host names). Compare a gateway's amount by value with `amount.equals(callback.amount)`.

### Start a payment

Each gateway takes a data object (`KbzPayPaymentData`, `WavePayPaymentData` with `WavePayItem`s, `AyaPayPaymentData` with an `AyaPayMethod`, `YomaMmqrPaymentData`, `CyberSourcePaymentData` with a `CyberSourceTransactionType`) and returns a typed result. `CyberSourcePaymentData`'s `currency`, `locale` and `transaction_type` have no defaults: set `currency` (e.g. `"MMK"`), `locale` (e.g. `"en-us"`) and `transaction_type` (e.g. `CyberSourceTransactionType.SALE`).

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

- `RedirectPayment` (`url`) from `kbz.pwa(data)` and `wave.initiate(data)`. Wave fills `data.merchant_reference_id` when empty; store it. Wave's `order_id` and `merchant_reference_id` may only hold letters, numbers, `_` and `-` (at most 100 characters); `callback_url`, `return_url` and `description` take at most 255. `WavePayPaymentData` also takes optional `merchant_name` and `time_to_live_seconds` (an `int` greater than 0) to override the configured values for one payment, and `additional_field_3` to `additional_field_5` (at most 50 characters each) for extra information Wave echoes in the callback.
- `FormPayment` from `aya.initiate(data)` and `cyber_source.initiate(data)` (no network call, never awaited): return `payment.to_html()` as an auto-submitting page (`payment.to_html(nonce)` adds your Content Security Policy nonce to its inline script; an empty nonce adds none), or render `action`, `fields` and `enctype` yourself.
- `QrPayment` from `kbz.qr(data)` (encode `qr_string`) and `yoma.initiate(data)` (`qr_image_data_uri()`, `expires_at`, `reference`). A Yoma QR lives `YomaMmqr.QR_LIFETIME_SECONDS` (120); renew it with `yoma.renew_qr(order_id)`.
- `AppPayment` from `kbz.app(data)`: send `payment.to_dict()` (`orderId`, `orderInfo`, `sign`, `signType`) to your mobile app.

AYA needs a channel: `aya.services()` lists `AyaPayService` entries (`key`, `supports(method)`), then `aya.initiate(AyaPayPaymentData(order_id="ORDER123", amount=1000, channel="kbz_pay", method=AyaPayMethod.QR))`.

### Handle the callback

Build a `CallbackRequest` from the raw request, verify it, then reply:

```python
from python_myanmar_payments import CallbackRequest, SignatureVerificationError

request = CallbackRequest(
    body=django_request.body,  # Flask: request.get_data(); FastAPI: await request.body()
    headers=django_request.headers,
    query=django_request.META["QUERY_STRING"],  # Flask: request.query_string
)
try:
    callback = kbz.handle_callback(request)
except SignatureVerificationError:  # bad signature, or a signed callback that is malformed
    return HttpResponse("invalid signature", status=400)
if callback.is_successful():
    ...  # compare callback.amount with the order, then fulfill callback.order_id once
ack = callback.acknowledgement
return HttpResponse(ack.body, status=ack.status, headers=ack.headers)
```

- Give the package the raw body bytes, the headers and the query string; `handle_callback()` and `verify_redirect()` are plain methods on the async classes too.
- Check AYA's browser return with `aya.verify_redirect(request)`.
- Yoma MMQR signs only the order number and status, so its callback has no `amount`: match the order number to your stored order (its QR was generated for the stored amount) and, to confirm, call `yoma.status(reference)` with that order's QR reference (`refLabel`). The status result has no amount either, so trust your stored amount.
- For production, store the verified callback, acknowledge immediately, then process it once in the background.

### Check status and handle errors

- `kbz.status(order_id)`, `aya.status(order_id)` and `yoma.status(reference)` return `PaymentStatusResult` with `status` and `is_successful()`. Wave Pay and CyberSource have no status API.
- Statuses: `PaymentStatus.SUCCESSFUL`, `PENDING`, `FAILED`, `CANCELED`, `EXPIRED`, `UNKNOWN`; `status.is_final()`.
- Gateway failures raise `ApiError` (`gateway_code`, `gateway_message`, `http_status`, `raw`; network errors are chained as `__cause__`). All errors extend `PaymentError`.

## Test your app

- Pass an `httpx.Client(transport=httpx.MockTransport(handler))` (or an `httpx.AsyncClient`) as `http_client`, or mock with `respx`.
- Replay a stored callback with `CallbackRequest.from_json(payload, headers)` or `CallbackRequest(body=..., headers=..., query=...)`; it is still signature-checked.
- To test your own fulfillment code, build `PaymentCallback(order_id="ORDER_1", status=PaymentStatus.SUCCESSFUL, gateway_status="PAY_SUCCESS")` yourself.

## Avoid

- Fulfilling orders from return pages or query strings; fulfill only from a verified callback or a status check.
- Treating `PENDING` or `UNKNOWN` as paid.
- Passing floats or `float(price)` as amounts; use `Amount.parse()` or a `Decimal`.
- Building the `CallbackRequest` from `request.json` / `request.POST` instead of the raw body.
- Reusing a Wave `merchant_reference_id`, or calling Yoma `initiate()` twice for one order (use `renew_qr()`).
- Opening a KBZ Pay PWA link outside a phone with the KBZ Pay app.
