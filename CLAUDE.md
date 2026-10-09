# Python Myanmar Payments

This repository is a framework-agnostic Python SDK for Myanmar payment gateways. Keep it focused, typed, and easy for Python developers (Django, Flask, FastAPI or plain Python) to install, test, and maintain.

## Package Conventions

- `httpx` is the only runtime dependency. Every gateway that calls an API has a sync class and an `Async*` twin; signing, validation and response parsing live once in the shared `_*Base` class, and the two clients only send requests.
- Match `php-myanmar-payments`, `go-myanmar-payments` and `node-myanmar-payments` exactly: gateways, validation limits, status maps, signatures, environment variables and errors. Reuse the shared vectors in `tests/fixtures`.
- Amounts are exact (`Amount`, `int`, decimal text or `Decimal`), never floats. Follow each gateway's official documentation for amounts, decimals, currencies and field limits.
- No HTTPS-only callback validation, no webhook storage, no refunds.
- Public names are exported from `python_myanmar_payments/__init__.py`; modules starting with `_` are internal.
- The version lives only in `src/python_myanmar_payments/_version.py` (PEP 440, e.g. `4.0.0a1`, tagged `v4.0.0-alpha.1`).

## Quick Commands

- Install: `uv sync`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Static analysis: `uv run mypy`
- Tests with coverage (100% lines and branches): `uv run pytest --cov`
- Build: `uv build`

## Agent Skill

`skills/python-myanmar-payments/SKILL.md` describes only how to use the package in an application. `tests/test_myanmar_payments.py` checks that every class and method it names exists.
