# FastAPI Myanmar Payments

Optional FastAPI integration for [python-myanmar-payments](https://pypi.org/project/python-myanmar-payments/): a lifespan that owns one payments facade, dependencies for its gateways, raw callback requests, verification that answers bad signatures with a 400, and the gateway's acknowledgement as a response.

> **You don't need this package to use FastAPI.** The core SDK already has async clients (`AsyncKbzPay`, `AsyncWavePay`, ...) that work in any FastAPI endpoint. This adapter only removes the framework glue. Payments, signing, callback verification, amounts and status mapping all stay in `python-myanmar-payments`; see its [documentation](https://laranex.vercel.app/python-myanmar-payments) for each gateway.

## Installation

Requires Python 3.10+, FastAPI 0.115+ and `python-myanmar-payments` 4.0.0a16 or later (below 5).

```bash
pip install fastapi-myanmar-payments
```

```bash
uv add fastapi-myanmar-payments
```

The version range names a pre-release, so pip and uv install the core's pre-releases without extra flags.

> **Pre-release core.** `python-myanmar-payments` 4.0.0 is still in alpha, and its pre-releases may contain breaking changes (4.0.0a16, for example, renamed Wave Money to Wave Pay). This adapter is tested against the oldest supported core (4.0.0a16) and the newest published one. Pin both packages in your application's lock file and upgrade them together.

## Quick start

```python
from fastapi import FastAPI, Response
from python_myanmar_payments import AsyncMyanmarPayments

from fastapi_myanmar_payments import (
    CallbackRequestDep,
    KbzPayDep,
    acknowledgement_response,
    payments_lifespan,
    verify_callback,
)

app = FastAPI(lifespan=payments_lifespan(AsyncMyanmarPayments.from_env))


@app.post("/payments/kbz/callback")
async def kbz_callback(kbz: KbzPayDep, request: CallbackRequestDep) -> Response:
    callback = verify_callback(kbz, request)  # 400 on a bad signature
    ...  # validate and persist it: see "Secure webhooks"
    return acknowledgement_response(callback.acknowledgement)
```

## Configuration

The adapter adds no settings. Configure the facade exactly as the core SDK documents ([Configuration](https://laranex.vercel.app/python-myanmar-payments/configuration)):

- `AsyncMyanmarPayments.from_env` reads `KBZ_PAY_*`, `WAVE_PAY_*`, `AYA_PAY_*`, `YOMA_MMQR_*`, `CYBER_SOURCE_*` and `MYANMAR_PAYMENTS_HTTP_TIMEOUT` when each gateway is first used.
- Or pass a factory that builds it from your own settings:

```python
from python_myanmar_payments import AsyncMyanmarPayments

def build_payments() -> AsyncMyanmarPayments:
    return AsyncMyanmarPayments(
        kbz_pay={"app_id": settings.kbz_app_id, ...},
        wave_pay={"merchant_id": settings.wave_merchant_id, ...},
    )

app = FastAPI(lifespan=payments_lifespan(build_payments))
```

Only the gateways you use need to be configured. Using one that is not raises the core `ConfigurationError` (`gateway`, `key`), which FastAPI answers with a 500.

## Lifespan

`payments_lifespan(...)` stores one `AsyncMyanmarPayments` on `app.state` for the life of the application, so every request reuses the same gateways, HTTP connections and Yoma MMQR token cache. Nothing is created per request.

Who closes what:

| You pass | At shutdown |
| --- | --- |
| A factory, e.g. `AsyncMyanmarPayments.from_env` | The lifespan calls `aclose()`, which closes only the HTTP clients the gateways created. |
| An instance, e.g. `payments_lifespan(my_payments)` | Nothing is closed; it is yours. |
| An `http_client` or `token_cache` inside the facade | Never closed by the adapter or the core SDK; close it yourself. |

Combine it with your own lifespan:

```python
from contextlib import asynccontextmanager

payments = payments_lifespan(AsyncMyanmarPayments.from_env)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with payments(app):
        # your own startup
        yield
        # your own shutdown


app = FastAPI(lifespan=lifespan)
```

Share your own `httpx.AsyncClient` (proxies, tracing, limits):

```python
import httpx


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(timeout=30) as http_client:  # you close it
        factory = lambda: AsyncMyanmarPayments.from_env(http_client=http_client)
        async with payments_lifespan(factory)(app):
            yield
```

## Dependency injection

| Dependency | Annotated alias | Gives |
| --- | --- | --- |
| `get_payments` | `PaymentsDep` | `AsyncMyanmarPayments` |
| `get_kbz_pay` | `KbzPayDep` | `AsyncKbzPay` |
| `get_wave_pay` | `WavePayDep` | `AsyncWavePay` |
| `get_aya_pay` | `AyaPayDep` | `AsyncAyaPay` |
| `get_yoma_mmqr` | `YomaMmqrDep` | `AsyncYomaMmqr` |
| `get_cyber_source` | `CyberSourceDep` | `CyberSource` |
| `callback_request` | `CallbackRequestDep` | the core `CallbackRequest` (raw body, headers and query string) |

Every gateway dependency reads the facade through `get_payments`, so overriding `get_payments` replaces them all. Without a lifespan (and without an override), `get_payments` raises a `RuntimeError` that says so.

## Async payment initiation

```python
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from python_myanmar_payments import Amount, ApiError, InvalidPaymentDataError, KbzPayPaymentData

from fastapi_myanmar_payments import KbzPayDep


@app.post("/orders/{order_id}/pay")
async def pay(order_id: str, kbz: KbzPayDep) -> RedirectResponse:
    order = await orders.get(order_id)  # your storage
    try:
        payment = await kbz.pwa(
            KbzPayPaymentData(
                order_id=order.id,
                amount=Amount.parse(order.amount),
                callback_url="https://shop.example/payments/kbz/callback",
            )
        )
    except InvalidPaymentDataError as error:
        raise HTTPException(422, detail=dict(error.errors)) from error
    except ApiError as error:
        raise HTTPException(502, detail="The payment gateway is unavailable.") from error
    return RedirectResponse(payment.url, status_code=303)
```

Don't retry a failed initiation automatically: the gateway may have created the payment before the error reached you. Check `kbz.status(order_id)` (KBZ Pay, AYA Pay; Yoma MMQR by QR reference) or let the customer try again with a new attempt where the gateway requires one (Wave Pay's `merchant_reference_id`).

`AyaPay.initiate()` and `CyberSource.initiate()` make no network call and return a `FormPayment`; return `HTMLResponse(payment.to_html())`.

## Secure webhooks

Use this order for every gateway callback: **verify → validate against the stored order → persist → acknowledge → process once.**

```python
from fastapi import Response
from python_myanmar_payments import Amount

from fastapi_myanmar_payments import (
    CallbackRequestDep,
    WavePayDep,
    acknowledgement_response,
    verify_callback,
)


@app.post("/payments/wave/callback")
async def wave_callback(wave: WavePayDep, request: CallbackRequestDep) -> Response:
    # 1. Verify. A bad signature or a malformed signed callback becomes a 400;
    #    nothing from an unverified payload is trusted, stored or echoed.
    callback = verify_callback(wave, request)

    # 2. Validate against your stored order, never against the callback alone.
    #    An unknown order is still recorded (step 3), never created or marked paid.
    order = await orders.get(callback.order_id)
    # Not every gateway sends an amount (Yoma MMQR doesn't): compare it when present.
    amount_matches = order is not None and (
        callback.amount is None or Amount.parse(order.amount).equals(callback.amount)
    )

    # 3. Persist durably and idempotently, e.g. a unique key on
    #    (gateway, order_id, gateway_status, gateway_reference). A duplicate
    #    delivery hits the key and is simply acknowledged again. If this raises,
    #    the request fails with a 5xx and nothing is acknowledged.
    await webhook_events.insert_once(
        gateway="wave_pay",
        order_id=callback.order_id,
        order_known=order is not None,
        status=callback.status,
        gateway_status=callback.gateway_status,
        gateway_reference=callback.gateway_reference,
        amount_matches=amount_matches,
        raw=dict(callback.raw),
    )

    # 4. Acknowledge only after step 3 has committed, with the gateway's exact response.
    return acknowledgement_response(callback.acknowledgement)


# 5. Process once, outside the request: a worker claims each stored event
#    (e.g. UPDATE ... SET processed_at = now() WHERE id = ? AND processed_at IS NULL),
#    fulfills the order only when it is known, PaymentStatus.SUCCESSFUL and
#    amount_matches, and flags unknown orders and mismatches for review.
```

Failure and retry behavior:

- **Verification fails** → 400. The gateway may retry; a forged or corrupted callback keeps failing. Log `error.__cause__` (a `SignatureVerificationError`) if you need it; its message never contains your secrets, but its `raw` payload is unverified.
- **Unknown order** (a verified callback for an order you don't have, e.g. from another environment or a deleted order) → record it durably as an unknown-order event, then return the gateway's acknowledgement so it stops retrying. Never create the order or mark anything paid from it; review it by hand.
- **Persisting fails** (database down) → let the exception produce a 5xx. Don't return the acknowledgement: the gateway retries later, so nothing is lost.
- **Persisted, but processing fails later** → the event stays unprocessed in your store; retry it from your worker. The gateway already has its acknowledgement and must not be relied on to resend.
- **Duplicates** are normal: gateways retry until they receive the acknowledgement. The idempotent insert and the once-only claim make them harmless.
- FastAPI `BackgroundTasks` run in the same process after the response; they are lost on a crash or deploy. Use them only for work you can redo from the persisted event.
- Never fulfill from a browser return or redirect; it is not proof of payment. For AYA Pay's signed return, `aya.verify_redirect(request)` shows the right page; fulfill from the callback or `status()`.
- Yoma MMQR also checks the `X-Webhook-Secret` header, which `CallbackRequestDep` passes through unchanged.

`callback_request` hands the core SDK the raw body bytes, the headers and the raw query string: it never decodes and re-encodes JSON or forms, so numbers like `1000.50` and form encoding reach the signature check exactly as sent. Don't read the body with `await request.json()` or `await request.form()` and rebuild it.

## Testing

No credentials or network are needed. Run the app in-process with `httpx.ASGITransport` and its own lifespan, and give the facade an `http_client` that answers from `httpx.MockTransport`. (FastAPI's `TestClient` works too, though recent Starlette versions ask for `httpx2` with it.)

```python
import httpx
import pytest
from python_myanmar_payments import AsyncMyanmarPayments

from fastapi_myanmar_payments import get_payments


def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"message": "success", "transaction_id": "T1"})


@pytest.mark.anyio
async def test_pay() -> None:
    fake = AsyncMyanmarPayments(
        wave_pay={...},  # test credentials
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    app.dependency_overrides[get_payments] = lambda: fake
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post("/orders/100/pay")
    assert response.status_code == 303
    app.dependency_overrides.clear()
```

- Override one gateway with `app.dependency_overrides[get_kbz_pay] = ...`.
- Test your fulfillment code with a `PaymentCallback(order_id=..., status=PaymentStatus.SUCCESSFUL, gateway_status=...)` you build yourself.
- Callbacks you post through `TestClient` are still signature-checked; sign them with your test secret (see the core SDK's [Testing](https://laranex.vercel.app/python-myanmar-payments/testing) guide).

## What this package does not do

No webhook router, no storage, no background processing, no retries and no automatic fulfillment: those decisions stay in your application. Request body size limits belong to your server or proxy.

## License

MIT. See [LICENSE](LICENSE.md).
