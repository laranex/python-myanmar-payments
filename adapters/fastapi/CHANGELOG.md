# Changelog

All notable changes to `fastapi-myanmar-payments` will be documented in this file. Releases are tagged `fastapi/v<version>`.

## fastapi/v0.1.0 - Unreleased

Initial release, for `python-myanmar-payments` 4.0.0a16 or later (below 5) and FastAPI 0.115 or later.

### Added
- `payments_lifespan()`: one `AsyncMyanmarPayments` per application on `app.state`, created from a factory and closed at shutdown, or an instance you own and close yourself.
- Dependencies `get_payments`, `get_kbz_pay`, `get_wave_pay`, `get_aya_pay`, `get_yoma_mmqr` and `get_cyber_source`, with `Annotated` aliases (`PaymentsDep`, `KbzPayDep`, ...).
- `callback_request` / `CallbackRequestDep`: the core `CallbackRequest` from the raw body bytes, headers and query string.
- `verify_callback()`: the gateway's `handle_callback()`, answering a `SignatureVerificationError` with a generic 400.
- `acknowledgement_response()`: the gateway's `Acknowledgement` as a response with its exact status, body and headers.
- An agent skill, `skills/fastapi-myanmar-payments/SKILL.md`, describing how to use the adapter in an application.

### Notes
- `python-myanmar-payments` 4.0.0 is still a pre-release; its pre-releases may contain breaking changes. The adapter is tested against 4.0.0a16 and the newest published core.
