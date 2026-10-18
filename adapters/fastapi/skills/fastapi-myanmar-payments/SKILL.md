---
name: fastapi-myanmar-payments
description: >
  Wire Myanmar payment gateways (KBZ Pay, Wave Pay, AYA Pay, Yoma MMQR, CyberSource) into a FastAPI app with fastapi-myanmar-payments: lifespan, gateway dependencies, verified callbacks and gateway acknowledgements.
license: MIT
metadata:
  author: Soe Thura
---

# FastAPI Myanmar Payments

## When to use

Use this skill when a FastAPI app takes payments with `python-myanmar-payments` and the project depends on `fastapi-myanmar-payments`. The adapter only connects FastAPI to the core SDK; payment data, amounts, gateway methods, statuses and errors are the core SDK's (see its `python-myanmar-payments` skill). Without the adapter, the core's async clients work in FastAPI directly.

## Install

```bash
pip install fastapi-myanmar-payments
```

Requires Python 3.10+, FastAPI 0.115+ and `python-myanmar-payments` 4.0.0a16 or later (below 5). The core is a pre-release whose versions may break each other: pin both packages and upgrade them together. Import the adapter from `fastapi_myanmar_payments` and everything else from `python_myanmar_payments`.

## Configure

Create the facade once per app with `payments_lifespan`; never build gateways or HTTP clients per request.

```python
from fastapi import FastAPI
from python_myanmar_payments import AsyncMyanmarPayments

from fastapi_myanmar_payments import payments_lifespan

app = FastAPI(lifespan=payments_lifespan(AsyncMyanmarPayments.from_env))
```

- A factory (`AsyncMyanmarPayments.from_env`, or a function returning `AsyncMyanmarPayments(kbz_pay={...}, ...)`) is created at startup and `aclose()`d at shutdown, which closes only the HTTP clients the core created. An instance you pass is never closed. An `http_client` or `token_cache` you give the facade is never closed by the adapter: close it yourself.
- With your own lifespan, nest it: `async with payments_lifespan(factory)(app): yield`.
- Settings and environment variables are the core's (`KBZ_PAY_*`, `WAVE_PAY_*`, ...). An unconfigured gateway raises the core `ConfigurationError`; a missing lifespan makes `get_payments` raise `RuntimeError`.

## Use

### Dependencies

`PaymentsDep` (`AsyncMyanmarPayments`), `KbzPayDep` (`AsyncKbzPay`), `WavePayDep` (`AsyncWavePay`), `AyaPayDep` (`AsyncAyaPay`), `YomaMmqrDep` (`AsyncYomaMmqr`) and `CyberSourceDep` (`CyberSource`) are `Annotated` aliases of `get_payments`, `get_kbz_pay`, `get_wave_pay`, `get_aya_pay`, `get_yoma_mmqr` and `get_cyber_source`. Await the async gateways' methods:

```python
from fastapi_myanmar_payments import KbzPayDep


@app.post("/orders/{order_id}/pay")
async def pay(order_id: str, kbz: KbzPayDep):
    payment = await kbz.pwa(data)  # data: the core's KbzPayPaymentData
    return RedirectResponse(payment.url, status_code=303)
```

Never retry a failed payment initiation automatically; check the status or start a new attempt.

### Handle the callback

Verify, validate against the stored order, persist, acknowledge, then process once:

```python
from fastapi import Response

from fastapi_myanmar_payments import (
    CallbackRequestDep,
    KbzPayDep,
    acknowledgement_response,
    verify_callback,
)


@app.post("/payments/kbz/callback")
async def kbz_callback(kbz: KbzPayDep, request: CallbackRequestDep) -> Response:
    callback = verify_callback(kbz, request)  # HTTPException(400) on a bad signature
    order = await orders.get(callback.order_id)
    await events.insert_once(callback, order_known=order is not None)  # durable, idempotent
    return acknowledgement_response(callback.acknowledgement)
```

- `CallbackRequestDep` (or `await callback_request(request)`) passes the raw body bytes, headers and query string to the core. Never rebuild the body from `await request.json()` or `await request.form()`.
- `verify_callback` calls the gateway's `handle_callback()` (sync or async clients, or `CyberSource`) and turns `SignatureVerificationError` into a generic 400 with the error chained as `__cause__`. Don't catch it to return 200.
- `acknowledgement_response` returns the gateway's exact status, body and headers (KBZ Pay's plain `success`, an empty `text/plain` 200 for most). Never write your own acknowledgement.
- Acknowledge only after the event is stored. If storing fails, let it raise a 5xx so the gateway retries.
- An unknown order: store it as an unknown-order event, then acknowledge. Never create the order or mark it paid.
- Not every gateway sends an amount (Yoma MMQR doesn't): compare `callback.amount` with the stored order when present, and fulfill only `callback.is_successful()` callbacks once, outside the request. FastAPI `BackgroundTasks` are lost on a crash.

## Test your app

- Override `app.dependency_overrides[get_payments]` with an `AsyncMyanmarPayments` whose `http_client` is `httpx.AsyncClient(transport=httpx.MockTransport(handler))`, or override one gateway, e.g. `app.dependency_overrides[get_kbz_pay]`.
- Run the app in-process: `async with app.router.lifespan_context(app)` and `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")`.
- Posted callbacks are still signature-checked; sign them with test secrets.

## Avoid

- Fulfilling orders from return pages or redirects.
- Creating `AsyncMyanmarPayments` or gateways inside an endpoint.
- Parsing and re-serializing callback bodies before verification.
- Acknowledging before the callback is durably stored.
- Adding signing, verification or status mapping to the app or the adapter: they belong to the core SDK.
