# Changelog

All notable changes to `django-myanmar-payments` will be documented in this file. Releases are tagged `django/v<version>`.

## django/v0.1.0 - Unreleased

Initial release, for `python-myanmar-payments` 4.0.0a16 or later (below 5) and Django 5.2 or later.

### Added
- The `MYANMAR_PAYMENTS` setting: gateway dicts or core config objects, or the core's environment variables when no gateway is set; `token_cache`, `http_client` and `async_http_client` options. Malformed settings raise `ImproperlyConfigured` naming the key, never the value.
- An optional app, `django_myanmar_payments`, whose system checks (`myanmar_payments.E001` to `E003`) report malformed settings, configured gateways missing a setting and unimportable client factories without calling any gateway.
- `get_payments()` (one sync `MyanmarPayments` per process, refusing to run in async code) and `get_async_payments()` (one `AsyncMyanmarPayments` per event loop), rebuilt when the settings change; `close_payments()` and `aclose_payments()`.
- `DjangoTokenCache` and `AsyncDjangoTokenCache`: Yoma MMQR tokens on a Django cache, shared by every worker process.
- `callback_request()`: the core `CallbackRequest` from the raw body, headers and query string.
- `verify_callback()`: the gateway's `handle_callback()`, raising `CallbackVerificationFailed` (a `SuspiciousOperation`, so Django answers 400 and logs to `django.security`).
- `acknowledgement_response()`: the gateway's `Acknowledgement` as an `HttpResponse` with its exact status, body and headers.
- An agent skill, `skills/django-myanmar-payments/SKILL.md`, describing how to use the adapter in an application.

### Notes
- `python-myanmar-payments` 4.0.0 is still a pre-release; its pre-releases may contain breaking changes. The adapter is tested against 4.0.0a16 and the newest published core.
