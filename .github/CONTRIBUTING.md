# Contribution Guide

Thank you for considering contributing to Python Myanmar Payments! Please review the following guidelines before submitting a pull request.

For significant changes, please open an issue first so we can discuss the approach.

## Process

1. Fork the project
2. Create a new branch
3. Code, test, commit, and push
4. Open a pull request detailing your changes

## Guidelines

- Keep `httpx` the only runtime dependency.
- Every gateway that calls an API has a sync and an async client; put signing, validation and response parsing in their shared base class so both stay identical.
- Validation rules, amount limits and currencies must match each gateway's official documentation; link the document in your pull request.
- Keep behavior in line with `php-myanmar-payments`, `go-myanmar-payments` and `node-myanmar-payments`, and reuse their test vectors in `tests/fixtures`.
- Keep `mypy --strict` clean and test coverage at 100% (lines and branches).
- Send a coherent commit history, making sure each commit in your pull request is meaningful.
- You may need to [rebase](https://git-scm.com/book/en/v2/Git-Branching-Rebasing) to avoid merge conflicts.
- Please remember that we follow [Semantic Versioning](https://semver.org/), written as [PEP 440](https://peps.python.org/pep-0440/) versions.

## Setup

Install [uv](https://docs.astral.sh/uv/), then:

```bash
uv sync
```

## Lint

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Tests

```bash
uv run pytest --cov
uv build
```
