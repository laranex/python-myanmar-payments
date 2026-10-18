# Django Myanmar Payments

Optional Django integration for [python-myanmar-payments](https://pypi.org/project/python-myanmar-payments/): gateways built from the `MYANMAR_PAYMENTS` setting (or the environment) for sync and async views, `manage.py check` validation, Yoma MMQR token caches on Django's cache, raw callback requests, verification that Django answers with a 400, and the gateway's acknowledgement as a response.

> **You don't need this package to use Django.** The core SDK already works in any Django view: sync clients (`KbzPay`, ...) in sync views and async clients (`AsyncKbzPay`, ...) in async views. This adapter only removes the Django glue. Payments, signing, callback verification, amounts and status mapping all stay in `python-myanmar-payments`; see its [documentation](https://laranex.vercel.app/python-myanmar-payments) for each gateway.

## Installation

Requires Python 3.10+, Django 5.2+ and `python-myanmar-payments` 4.0.0a16 or later (below 5).

```bash
pip install django-myanmar-payments
```

```bash
uv add django-myanmar-payments
```

Django 5.2 is the oldest Django release that still gets security fixes. Django 6.x needs Python 3.12+.

> **Pre-release core.** `python-myanmar-payments` 4.0.0 is still in alpha, and its pre-releases may contain breaking changes (4.0.0a16, for example, renamed Wave Money to Wave Pay). This adapter is tested against the oldest supported core (4.0.0a16) and the newest published one. Pin both packages in your lock file and upgrade them together.

## Quick start

```python
# settings.py
INSTALLED_APPS = [
    ...,
    "django_myanmar_payments",  # optional: adds MYANMAR_PAYMENTS to manage.py check
]

MYANMAR_PAYMENTS = {
    "kbz_pay": {
        "app_id": env("KBZ_PAY_APP_ID"),
        "app_key": env("KBZ_PAY_APP_KEY"),
        "merchant_code": env("KBZ_PAY_MERCHANT_CODE"),
        "api_url": "https://api.kbzpay.com/payment/gateway",
        "pwa_url": "https://wap.kbzpay.com/pgw/pwa/#/",
        "time_to_live_minutes": 15,
    },
}
```

```python
# views.py
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from django_myanmar_payments import acknowledgement_response, get_payments, verify_callback


@csrf_exempt  # the gateway can't send a CSRF token; its signature is checked instead
@require_POST
def kbz_callback(request):
    callback = verify_callback(get_payments().kbz_pay(), request)  # 400 on a bad signature
    ...  # validate and persist it: see "Secure webhooks"
    return acknowledgement_response(callback.acknowledgement)
```

## Configuration

`MYANMAR_PAYMENTS` is a dict. Every key is optional.

| Key | Value |
| --- | --- |
| `kbz_pay`, `wave_pay`, `aya_pay`, `yoma_mmqr`, `cyber_source` | A dict of the core config's keyword arguments, or the core config object (`KbzPayConfig(...)`, ...). See the core's [Configuration](https://laranex.vercel.app/python-myanmar-payments/configuration). |
| `token_cache` | A `CACHES` alias that stores Yoma MMQR access tokens, e.g. `"default"`. |
| `http_client` | The dotted path of a function returning a new `httpx.Client` for the sync gateways. |
| `async_http_client` | The dotted path of a function returning a new `httpx.AsyncClient` for the async gateways. |

**Environment variables.** Without any gateway key (or without the setting at all), every gateway reads the core's variables (`KBZ_PAY_*`, `WAVE_PAY_*`, `AYA_PAY_*`, `YOMA_MMQR_*`, `CYBER_SOURCE_*` and `MYANMAR_PAYMENTS_HTTP_TIMEOUT`) when it is first used:

```python
MYANMAR_PAYMENTS = {"token_cache": "default"}  # gateways from the environment
```

With gateway keys, only those gateways are configured; the others raise the core `ConfigurationError` when used. Either way, gateways you never use need no credentials.

**Errors.** A malformed setting (an unknown key, a gateway that isn't a dict or config object, an empty option, a `token_cache` that isn't in `CACHES`, a factory that can't be imported or returns the wrong type) raises `ImproperlyConfigured`. Its message names the key, never the value. With the app in `INSTALLED_APPS`, `manage.py check` reports the same problems, and a configured gateway missing a setting, without calling any gateway:

| Check | Meaning |
| --- | --- |
| `myanmar_payments.E001` | `MYANMAR_PAYMENTS` is malformed. |
| `myanmar_payments.E002` | A configured gateway is missing a setting or has an invalid one. |
| `myanmar_payments.E003` | An `http_client` / `async_http_client` path can't be imported. |

**Credentials.** Error messages and the adapter's logs never contain setting values. Django's debug pages mask the secret keys inside `MYANMAR_PAYMENTS` (`app_key`, `secret_key`, `app_secret`, `client_secret`, `webhook_secret`, `webhook_hash_key`, `access_key`), and the core's config objects leave secrets out of their `repr`. Keep the values themselves out of your settings file (environment variables or a secrets manager).

## Gateways in sync and async views

| Function | Gives | Use in |
| --- | --- | --- |
| `get_payments()` | `MyanmarPayments` (sync gateways) | Sync views, management commands, task workers |
| `get_async_payments()` | `AsyncMyanmarPayments` (async gateways) | Async views (ASGI) |

- Both are built from the settings on first use and reused: gateways and HTTP connections aren't created per request.
- `get_payments()` is shared by every thread of the process. In async code it raises Django's `SynchronousOnlyOperation` instead of blocking the event loop; call it through `sync_to_async` or use `get_async_payments()`.
- An `httpx.AsyncClient` belongs to the event loop it first ran on, so `get_async_payments()` keeps one facade per running loop. Under ASGI that's one per worker process. Under WSGI, Django runs each async view in a new event loop, so async gateways can't reuse connections there: use sync views and `get_payments()` with WSGI.
- Unless you set `token_cache`, every facade in the process shares one in-memory Yoma MMQR token. With several worker processes, set `token_cache` to a shared cache (Redis, Memcached, database) so they share one token too. `DjangoTokenCache` and `AsyncDjangoTokenCache` (`cache.aget()`/`aset()`), used automatically, can also be passed to core gateways yourself.
- Changing `MYANMAR_PAYMENTS` or `CACHES` at runtime (`override_settings` in tests) rebuilds the facades from the new settings.

### Starting a payment

```python
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from python_myanmar_payments import Amount, ApiError, InvalidPaymentDataError, KbzPayPaymentData

from django_myanmar_payments import get_async_payments, get_payments


def data(order):
    return KbzPayPaymentData(
        order_id=str(order.pk),
        amount=Amount.of(order.amount),  # a DecimalField, never a float
        callback_url="https://shop.example/payments/kbz/callback",
    )


def pay(request, order_id):  # sync view
    order = Order.objects.get(pk=order_id)
    try:
        payment = get_payments().kbz_pay().pwa(data(order))
    except InvalidPaymentDataError as error:
        return HttpResponseBadRequest(", ".join(error.errors.values()))
    except ApiError:
        return HttpResponse("The payment gateway is unavailable.", status=502)
    return HttpResponseRedirect(payment.url)


async def apay(request, order_id):  # async view
    order = await Order.objects.aget(pk=order_id)
    payment = await get_async_payments().kbz_pay().pwa(data(order))
    return HttpResponseRedirect(payment.url)
```

Don't retry a failed initiation automatically: the gateway may have created the payment before the error reached you. Check `status()` (KBZ Pay, AYA Pay; Yoma MMQR by QR reference) or start a new attempt. AYA Pay and CyberSource return a `FormPayment` without a network call: `HttpResponse(payment.to_html())`.

## Secure webhooks

Use this order for every gateway callback: **verify → validate against the stored order → persist → acknowledge → process once.**

```python
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from python_myanmar_payments import Amount

from django_myanmar_payments import acknowledgement_response, get_payments, verify_callback


@csrf_exempt
@require_POST
def wave_callback(request):
    # 1. Verify. A bad signature or a malformed signed callback raises
    #    CallbackVerificationFailed: Django answers 400 and logs it to
    #    django.security.CallbackVerificationFailed, without the payload.
    callback = verify_callback(get_payments().wave_pay(), request)

    # 2. Validate against your stored order, never against the callback alone.
    #    An unknown order is still recorded (step 3), never created or marked paid.
    order = Order.objects.filter(pk=callback.order_id).first()
    # Not every gateway sends an amount (Yoma MMQR doesn't): compare it when present.
    amount_matches = order is not None and (
        callback.amount is None or Amount.of(order.amount).equals(callback.amount)
    )

    # 3. Persist durably and idempotently: a unique constraint on
    #    (gateway, order_id, gateway_status, gateway_reference) makes a duplicate
    #    delivery a no-op. If this raises, Django answers 500 and nothing is acknowledged.
    with transaction.atomic():
        event, created = WebhookEvent.objects.get_or_create(
            gateway="wave_pay",
            order_id=callback.order_id,
            gateway_status=callback.gateway_status,
            gateway_reference=callback.gateway_reference or "",
            defaults={
                "order_known": order is not None,
                "status": callback.status,
                "amount_matches": amount_matches,
                "raw": dict(callback.raw),
            },
        )
        if created:
            # 5. Process once, after the commit, in your task queue.
            transaction.on_commit(lambda: process_webhook_event.delay(event.pk))

    # 4. Acknowledge only after step 3 has committed, with the gateway's exact response.
    return acknowledgement_response(callback.acknowledgement)
```

The worker claims each event once (e.g. `WebhookEvent.objects.filter(pk=pk, processed_at=None).update(processed_at=now())` returns 1), fulfills the order only when it is known, `PaymentStatus.SUCCESSFUL` and `amount_matches`, and flags unknown orders and mismatches for review. The same view works as an `async def` with `get_async_payments()`, the async ORM methods (`aget()`, `afirst()`) and `sync_to_async` around the transaction, which Django runs synchronously.

Failure and retry behavior:

- **Verification fails** → 400, logged to `django.security.CallbackVerificationFailed` with the core `SignatureVerificationError` as the cause. The gateway may retry; a forged or corrupted callback keeps failing. Nothing from the unverified payload is stored or echoed.
- **Unknown order** (a verified callback for an order you don't have, e.g. from another environment or a deleted order) → record it durably as an unknown-order event, then return the gateway's acknowledgement so it stops retrying. Never create the order or mark anything paid from it; review it by hand.
- **Persisting fails** (database down) → let the exception produce a 500. Don't return the acknowledgement: the gateway retries later, so nothing is lost.
- **Persisted, but processing fails later** → the event stays unprocessed; retry it from your worker. The gateway already has its acknowledgement and must not be relied on to resend.
- **Duplicates** are normal: gateways retry until they receive the acknowledgement. The unique constraint and the once-only claim make them harmless.
- Never fulfill from a browser return or redirect; it is not proof of payment. For AYA Pay's signed return, `get_payments().aya_pay().verify_redirect(callback_request(request))` shows the right page; fulfill from the callback or `status()`.
- Webhook views must be `csrf_exempt`: gateways can't send Django's CSRF token. Their signature (and Yoma MMQR's `X-Webhook-Secret` header) authenticates them instead.

`callback_request(request)` (used by `verify_callback`) hands the core SDK `request.body`, `request.headers` and the raw query string: it never decodes and re-encodes JSON or forms, so numbers like `1000.50` reach the signature check exactly as sent. Don't build it from `request.POST` or `json.loads(request.body)`.

## Resource cleanup

Django has no application shutdown hook, so the facades live until the process exits. Close them explicitly where you own the lifecycle:

- `close_payments()` closes the sync facade's HTTP clients (the ones the gateways created and the one from your `http_client` factory); the next `get_payments()` builds new ones.
- `await aclose_payments()` does the same for the running event loop's async facade.
- Clients returned by your `http_client` / `async_http_client` factories belong to the adapter: return a new client each call, not a shared one. Caches are never closed.

## Testing

No credentials or network are needed. Point the HTTP client settings at `httpx.MockTransport` and override the setting per test; the facades are rebuilt from it:

```python
# tests/fakes.py
import httpx


def handler(request):
    return httpx.Response(200, json={"message": "success", "transaction_id": "T1"})


def client():
    return httpx.Client(transport=httpx.MockTransport(handler))
```

```python
from django.test import TestCase, override_settings

TEST_PAYMENTS = {"wave_pay": {...}, "http_client": "tests.fakes.client"}  # test credentials


@override_settings(MYANMAR_PAYMENTS=TEST_PAYMENTS)
class PayTests(TestCase):
    def test_pay(self):
        response = self.client.post("/orders/100/pay")
        self.assertEqual(response.status_code, 302)
```

- Callbacks you post with `self.client.post(url, data=raw_bytes, content_type="application/json")` are still signature-checked; sign them with your test secrets (see the core SDK's [Testing](https://laranex.vercel.app/python-myanmar-payments/testing) guide).
- Test your fulfillment code with a `PaymentCallback(order_id=..., status=PaymentStatus.SUCCESSFUL, gateway_status=...)` you build yourself.

## What this package does not do

No models, no migrations, no URL patterns or views, no task queue, no retries and no automatic fulfillment: those stay in your project. Callback bodies are limited by Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` (2.5 MB by default), far above any gateway's callback.

## License

MIT. See [LICENSE](LICENSE.md).
