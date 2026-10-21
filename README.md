# Python Myanmar Payments

[![PyPI](https://img.shields.io/pypi/v/python-myanmar-payments.svg?style=flat-square)](https://pypi.org/project/python-myanmar-payments/)
[![Tests](https://github.com/laranex/python-myanmar-payments/actions/workflows/tests.yml/badge.svg)](https://github.com/laranex/python-myanmar-payments/actions/workflows/tests.yml)
[![Python](https://img.shields.io/pypi/pyversions/python-myanmar-payments.svg?style=flat-square)](https://pypi.org/project/python-myanmar-payments/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)](LICENSE.md)

Python SDK for Myanmar payment gateways: KBZ Pay, Wave Pay, Wave Pay Mini App, AYA Pay, Yoma MMQR and CyberSource. Typed requests and results, exact amounts, sync and async clients. Built for humans and AI agents.

Every Myanmar payment gateway signs its requests and callbacks its own way, reports its own status codes and has its own rules for amounts. Python Myanmar Payments handles all of that behind one typed API for Django, Flask, FastAPI and any other framework: build the payment data, start the payment, then verify the callback the gateway sends back.

## Why Python Myanmar Payments

- **Five gateways, one pattern.** KBZ Pay (PWA, QR and in-app), Wave Pay, AYA Pay, Yoma MMQR and CyberSource, each with its own class and the same three steps.
- **One typed result per flow.** `RedirectPayment`, `FormPayment`, `QrPayment` or `AppPayment` holds exactly what the next step needs: a URL to redirect to, an auto-submitting form, a QR code or the signed order for the KBZ Pay mobile SDK. See [Payment Flows](https://laranex.vercel.app/python-myanmar-payments/payment-flows).
- **Exact amounts, checked against each gateway's rules.** An `Amount` is kept as decimal text, takes an `int` of any size or a `Decimal`, and never passes through a `float`. Decimals, currency and limits follow each gateway's official documentation and are checked before any request is sent. See [Amounts](https://laranex.vercel.app/python-myanmar-payments/amounts).
- **Verified callbacks.** `handle_callback()` checks the gateway's signature without a network call, maps its status onto one `PaymentStatus` enum and gives you the acknowledgement the gateway expects. When a callback is late, ask KBZ Pay, AYA Pay or Yoma MMQR with `status()`. See [Callbacks & Status](https://laranex.vercel.app/python-myanmar-payments/callbacks).
- **Errors that say what went wrong.** A missing setting, an invalid field, a gateway error and a bad signature each raise their own exception, with a message that names the setting, the field or the gateway's code.
- **Sync and async.** Every gateway that calls an API has a sync class for Django, Flask and other WSGI code and an async twin with the same methods for FastAPI, Starlette and async Django views. The only dependency is `httpx`, and the package is fully typed and checked with `mypy --strict`.
- **Same behavior in every language.** The PHP, Go, Node.js and Python SDKs pass one shared set of test vectors for signing and callback verification, and read the same environment variables, so one `.env` file and one Yoma MMQR token cache serve them all.
- **Nothing stored for you.** The package keeps no tables and no webhook log. [Handling Webhooks](https://laranex.vercel.app/python-myanmar-payments/webhooks) shows the recommended flow as code you copy: verify, store, acknowledge, then process each call once.
- **Easy to test.** Every gateway call goes through `httpx`, so your tests fake the responses with `httpx.MockTransport` or respx, replay signed callbacks and never touch the network. See [Testing](https://laranex.vercel.app/python-myanmar-payments/testing).

## Installation

> **Requires** Python 3.10+. The only dependency is [`httpx`](https://www.python-httpx.org/), which sends the gateway requests.

```bash
pip install python-myanmar-payments
```

```bash
uv add python-myanmar-payments
```

```bash
poetry add python-myanmar-payments
```

### FastAPI adapter (optional)

The SDK works with FastAPI as is: use the async clients in your endpoints. The separate [`fastapi-myanmar-payments`](adapters/fastapi) package adds optional conveniences on top of it: a lifespan that owns one `AsyncMyanmarPayments`, gateway dependencies, raw callback requests and acknowledgement responses. It is versioned and released independently (`fastapi/v*` tags).

```bash
pip install fastapi-myanmar-payments
```

### Django adapter (optional)

The SDK works with Django as is: sync clients in sync views, async clients in async views. The separate [`django-myanmar-payments`](adapters/django) package adds optional conveniences on top of it: gateways from a `MYANMAR_PAYMENTS` setting with `manage.py check` validation, sync and async access, Yoma MMQR token caches on Django's cache, raw callback requests and acknowledgement responses. It is versioned and released independently (`django/v*` tags).

```bash
pip install django-myanmar-payments
```

## Documentation

The full documentation lives at **[laranex.vercel.app/python-myanmar-payments](https://laranex.vercel.app/python-myanmar-payments)**:

- [Introduction](https://laranex.vercel.app/python-myanmar-payments/introduction): why the package exists and what it covers
- [Installation](https://laranex.vercel.app/python-myanmar-payments/installation): requirements, install command and a quick start
- [AI Agent Skill](https://laranex.vercel.app/python-myanmar-payments/ai-agent-skill): the skill that teaches coding agents to use the package
- [Configuration](https://laranex.vercel.app/python-myanmar-payments/configuration): credentials, environment variables, endpoints, UAT, HTTP client and token cache
- [Amounts](https://laranex.vercel.app/python-myanmar-payments/amounts): exact amounts and each gateway's amount rules
- [Payment Flows](https://laranex.vercel.app/python-myanmar-payments/payment-flows): redirect, form, QR and in-app payments
- [Callbacks & Status](https://laranex.vercel.app/python-myanmar-payments/callbacks): verified callbacks, payment statuses and status checks
- [Handling Webhooks](https://laranex.vercel.app/python-myanmar-payments/webhooks): the recommended verify, store, acknowledge, process-once flow
- [Framework Integration](https://laranex.vercel.app/python-myanmar-payments/framework-integration): Django, Flask and FastAPI, sync and async
- [Testing](https://laranex.vercel.app/python-myanmar-payments/testing): faking gateway calls and replaying signed callbacks in your tests
- [KBZ Pay](https://laranex.vercel.app/python-myanmar-payments/drivers/kbz-pay): PWA redirect, QR and in-app payments, status checks and callbacks
- [Wave Pay](https://laranex.vercel.app/python-myanmar-payments/drivers/wave-pay): redirect payments with line items and verified callbacks
- [Wave Pay Mini App](https://laranex.vercel.app/python-myanmar-payments/drivers/wave-pay-mini-app): access tokens, signed payment payloads for Wave's JavaScript SDK and verified callbacks
- [AYA Pay](https://laranex.vercel.app/python-myanmar-payments/drivers/aya-pay): hosted checkout for AYA Pay, other wallets and cards, with status checks
- [Yoma MMQR](https://laranex.vercel.app/python-myanmar-payments/drivers/yoma-mmqr): QR images, QR renewal, status checks and verified callbacks
- [CyberSource](https://laranex.vercel.app/python-myanmar-payments/drivers/cyber-source): Secure Acceptance card payments through a signed hosted checkout form
- [Results](https://laranex.vercel.app/python-myanmar-payments/references/results): every property of the payment result types
- [PaymentCallback & Status](https://laranex.vercel.app/python-myanmar-payments/references/payment-callback): callbacks, status results, acknowledgements and the PaymentStatus values
- [Errors](https://laranex.vercel.app/python-myanmar-payments/references/errors): validation, API, signature and configuration errors

## License

MIT. See [LICENSE](LICENSE.md).
