# Changelog

All notable changes to `python-myanmar-payments` will be documented in this file.

## v4.0.0 - Unreleased

Initial release. The version number matches the other Laranex Myanmar payments packages (`php-myanmar-payments`, `laravel-myanmar-payments`, `go-myanmar-payments`, `node-myanmar-payments`), and the gateways, flows, validation, signing, status maps, results and errors are a port of them, sharing their test vectors. Pre-releases are tagged `v4.0.0-alpha.N` and published to PyPI as `4.0.0aN`.

### Added
- Gateways: KBZ Pay (`pwa`, `qr`, `app`, `status`), Wave Money (`initiate`), AYA Payment Gateway (`services`, `initiate`, `status`, `verify_redirect`), Yoma MMQR (`initiate`, `renew_qr`, `status`, `forget_token`) and CyberSource Secure Acceptance (`initiate`); every gateway validates its payment data (`KbzPay.validate()` and so on) and verifies callbacks with `handle_callback`.
- A sync and an async client for every gateway that calls an API (`KbzPay` / `AsyncKbzPay`, `WaveMoney` / `AsyncWaveMoney`, `AyaPay` / `AsyncAyaPay`, `YomaMmqr` / `AsyncYomaMmqr`), sharing one implementation of signing, validation and response parsing. `CyberSource` makes no network calls and serves both.
- `httpx` is the only runtime dependency: pass your own `httpx.Client` / `httpx.AsyncClient` as `http_client`, or let the gateway create one with a 30 second `timeout` and close it with `close()` / `aclose()` or a `with` / `async with` block.
- Exact `Amount` type (`Amount.kyat()`, `Amount.parse()`, `Amount.of()`) kept as decimal text, so amounts never pass through a float; payment data also takes a whole `int`, decimal text or a `Decimal`, and rejects floats. Decimals are accepted only where the gateway documents them (KBZ Pay up to 2 places, CyberSource any); every gateway except CyberSource is MMK only.
- Typed payment data dataclasses (`KbzPayPaymentData`, `WaveMoneyPaymentData` with `WaveMoneyItem`, `AyaPayPaymentData`, `YomaMmqrPaymentData`, `CyberSourcePaymentData`) and a config class per gateway with `from_env()` (default `os.environ`), reading the same `*_SANDBOX`, credential and URL variables as the PHP, Laravel, Go and Node packages; a missing credential raises `ConfigurationError`. `MyanmarPayments` and `AsyncMyanmarPayments` build every gateway lazily from config objects or the environment.
- Result classes: `RedirectPayment`, `FormPayment` (with `to_html()`), `QrPayment` (with `qr_image_data_uri()`) and `AppPayment` (with `to_dict()`); `PaymentCallback` (with a gateway-independent `PaymentStatus` and the `Acknowledgement` each gateway expects, from `acknowledgement()`) and `PaymentStatusResult`.
- `CallbackRequest(body=..., headers=..., query=...)` takes the raw body bytes, headers and query string from Django, Flask, FastAPI or any other framework; `CallbackRequest.from_json()` replays a stored callback. JSON numbers in callbacks are read without a float conversion, so signatures and amounts keep the gateway's exact text, and `raw` holds `int` and `Decimal` values.
- Errors: `PaymentError` and its subclasses `InvalidPaymentDataError` (per-field `errors`), `ApiError` (`gateway_code`, `gateway_message`, `http_status`, `raw`, network errors chained as `__cause__`), `SignatureVerificationError` and `ConfigurationError`.
- An injectable `TokenCache` (or `AsyncTokenCache` for the async client) with a thread-safe in-memory default for Yoma's access token; concurrent calls share one token request.
- CyberSource callbacks trust only the fields listed in `signed_field_names`: `decision` and `req_reference_number` must be signed, and unsigned fields are left out of the result and `raw`.
- AYA Pay reads a `payload` whose `+` signs became spaces in an unencoded return query string.
- Callback, return and cancel URLs only need to be valid absolute http or https URLs; there is no HTTPS-only rule (gateways may still require HTTPS in production).
- Wave Money's sandbox is `https://preprodpayments.wavemoney.io:8107`, with checkout at `https://preprodpayments.wavemoney.io/authenticate`.
- Fully typed (`py.typed`, `mypy --strict`).
- Agent skill in `skills/python-myanmar-payments` so coding agents use the package correctly; install it with `npx skills add laranex/python-myanmar-payments`.
- Requires Python 3.10 or higher.
