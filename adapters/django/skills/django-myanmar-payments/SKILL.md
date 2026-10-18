---
name: django-myanmar-payments
description: >
  Wire Myanmar payment gateways (KBZ Pay, Wave Pay, AYA Pay, Yoma MMQR, CyberSource) into a Django project with django-myanmar-payments: MYANMAR_PAYMENTS settings, sync and async gateways, verified callbacks and gateway acknowledgements.
license: MIT
metadata:
  author: Soe Thura
---

# Django Myanmar Payments

## When to use

Use this skill when a Django project takes payments with `python-myanmar-payments` and depends on `django-myanmar-payments`. The adapter only connects Django to the core SDK; payment data, amounts, gateway methods, statuses and errors are the core SDK's (see its `python-myanmar-payments` skill). Without the adapter, the core's clients work in Django views directly.

## Install

```bash
pip install django-myanmar-payments
```

Requires Python 3.10+, Django 5.2+ and `python-myanmar-payments` 4.0.0a16 or later (below 5). The core is a pre-release whose versions may break each other: pin both packages and upgrade them together. Import the adapter from `django_myanmar_payments` and everything else from `python_myanmar_payments`.

## Configure

```python
INSTALLED_APPS = [..., "django_myanmar_payments"]  # optional: manage.py check validates the setting

MYANMAR_PAYMENTS = {
    "kbz_pay": {
        "app_id": ...,
        "app_key": ...,
        "merchant_code": ...,
        "api_url": ...,
        "pwa_url": ...,
        "time_to_live_minutes": 15,
    },
    "token_cache": "default",  # optional CACHES alias for Yoma MMQR tokens
}
```

- Gateway keys: `kbz_pay`, `wave_pay`, `aya_pay`, `yoma_mmqr`, `cyber_source`, each a dict of the core config's keyword arguments or a core config object. Without any gateway key (or without the setting), gateways read the core's environment variables (`KBZ_PAY_*`, ...) when first used. Unused gateways need no credentials.
- Options: `token_cache` (a `CACHES` alias, used through `DjangoTokenCache` / `AsyncDjangoTokenCache`, which core gateways also accept; use a shared cache with several worker processes), `http_client` and `async_http_client` (dotted paths of functions returning a new `httpx.Client` / `httpx.AsyncClient`; the adapter closes what they return).
- A malformed setting raises `ImproperlyConfigured`; a configured gateway missing a setting raises the core `ConfigurationError` when used and is reported by `manage.py check` (`myanmar_payments.E002`). Keep credential values out of settings files.

## Use

### Gateways

- Sync views, commands and task workers: `get_payments().kbz_pay()` (also `wave_pay()`, `aya_pay()`, `yoma_mmqr()`, `cyber_source()`). One facade per process, shared by threads. It raises `SynchronousOnlyOperation` in async code.
- Async views under ASGI: `get_async_payments().kbz_pay()` and await its methods. One facade per event loop. Under WSGI use sync views: async views there get a new event loop per request.
- Never create gateways or HTTP clients per request. Close the process's clients with `close_payments()` / `await aclose_payments()` when you own the lifecycle.

```python
from django_myanmar_payments import get_async_payments, get_payments

payment = get_payments().kbz_pay().pwa(data)  # data: the core's KbzPayPaymentData
payment = await get_async_payments().kbz_pay().pwa(data)
```

Never retry a failed payment initiation automatically; check the status or start a new attempt.

### Handle the callback

Verify, validate against the stored order, persist, acknowledge, then process once:

```python
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from django_myanmar_payments import acknowledgement_response, get_payments, verify_callback


@csrf_exempt
@require_POST
def kbz_callback(request):
    callback = verify_callback(get_payments().kbz_pay(), request)
    order = Order.objects.filter(pk=callback.order_id).first()
    record_event_once(callback, order_known=order is not None)  # durable, idempotent
    return acknowledgement_response(callback.acknowledgement)
```

- Webhook views are `csrf_exempt` and `require_POST`; the gateway's signature authenticates them.
- `verify_callback(gateway, request)` builds the core `CallbackRequest` with `callback_request(request)` (raw `request.body`, headers, query string) and calls `handle_callback()`. A bad signature raises `CallbackVerificationFailed`, a `SuspiciousOperation`: Django answers 400 and logs it. Don't catch it to return 200.
- Never build the callback from `request.POST` or `json.loads(request.body)`.
- `acknowledgement_response(callback.acknowledgement)` returns the gateway's exact status, body and headers. Never write your own acknowledgement.
- Acknowledge only after the event is committed (unique constraint, `transaction.atomic`). If storing fails, let it raise so the gateway retries.
- An unknown order: store it as an unknown-order event, then acknowledge. Never create the order or mark it paid.
- Not every gateway sends an amount (Yoma MMQR doesn't): compare `callback.amount` with the stored order when present (`Amount.of(order.amount).equals(callback.amount)`), and fulfill `callback.is_successful()` callbacks once, after commit, in a task queue (`transaction.on_commit`).
- AYA Pay's browser return: `get_payments().aya_pay().verify_redirect(callback_request(request))`; it is not proof of payment.

## Test your app

- `override_settings(MYANMAR_PAYMENTS={..., "http_client": "tests.fakes.client"})` with a function returning `httpx.Client(transport=httpx.MockTransport(handler))`; the facades are rebuilt from the overridden setting.
- Post signed callbacks with `self.client.post(url, data=raw_bytes, content_type="application/json")`; they are still signature-checked.

## Avoid

- Fulfilling orders from return pages or redirects.
- Calling `get_payments()` in async views, or async gateways under WSGI.
- Parsing and re-serializing callback bodies before verification.
- Acknowledging before the callback is durably stored.
- Adding signing, verification or status mapping to the project or the adapter: they belong to the core SDK.
