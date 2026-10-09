"""Shared internals: hashing, time, environment variables and settings."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, TypeVar
from urllib.parse import quote_plus

from ._errors import ConfigurationError

EnvSource = Mapping[str, str]
"""Environment variables, e.g. ``os.environ``."""


# --- Hashing -------------------------------------------------------------------


def hmac_sha256_hex(key: str, message: str) -> str:
    """HMAC-SHA256 of ``message``, hex encoded."""
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def hmac_sha256_base64(key: str, message: str) -> str:
    """HMAC-SHA256 of ``message``, base64 encoded."""
    digest = hmac.new(key.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def sha256_hex(message: str) -> str:
    """SHA-256 of ``message``, hex encoded."""
    return hashlib.sha256(message.encode()).hexdigest()


def safe_equal(expected: str, actual: str) -> bool:
    """Compares two strings in constant time (for equal lengths)."""
    return hmac.compare_digest(expected.encode(), actual.encode())


def random_hex(size: int = 16) -> str:
    """``size`` random bytes, hex encoded."""
    return secrets.token_hex(size)


def query_escape(value: str) -> str:
    """Escapes text like Go's ``url.QueryEscape``.

    Everything except letters, digits and ``-_.~`` is percent-encoded and spaces
    become ``+``.
    """
    return quote_plus(value, safe="")


_BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")


def decode_base64(value: str) -> str | None:
    """Decodes standard base64 as UTF-8 text, or ``None``.

    The padding is either complete or left out entirely; partial padding, other
    alphabets, whitespace and bytes that are not UTF-8 are rejected.
    """
    if _BASE64.fullmatch(value) is None:
        return None
    if "=" in value:
        if len(value) % 4 != 0:
            return None
    elif len(value) % 4 == 1:
        return None
    else:
        value += "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except ValueError:  # Invalid UTF-8 (UnicodeDecodeError is a ValueError).
        return None


# --- Time ----------------------------------------------------------------------


def current_time() -> float:
    """Seconds since the epoch. Tests replace it to freeze the clock."""
    return time.time()


def unix_time() -> int:
    """Whole seconds since the epoch."""
    return int(current_time())


def utc_now() -> datetime:
    """The current time as an aware UTC datetime."""
    return datetime.fromtimestamp(current_time(), tz=timezone.utc)


# --- Settings ------------------------------------------------------------------


def default_env() -> EnvSource:
    """The default environment: ``os.environ``."""
    return os.environ


def env_first(env: EnvSource, *keys: str) -> str:
    """The first non-empty value among ``keys``, trimmed, or ``""``."""
    for key in keys:
        value = (env.get(key) or "").strip()
        if value:
            return value
    return ""


_FALSE = frozenset({"false", "0", "f", "no", "off"})


def env_sandbox(env: EnvSource, key: str) -> bool:
    """Reads a ``*_SANDBOX`` variable.

    Only ``false``, ``0``, ``f``, ``no`` and ``off`` (any case) select production;
    unset or unrecognized values mean sandbox.
    """
    return sandbox_flag(env.get(key) or "")


def sandbox_flag(value: bool | str) -> bool:
    """A ``sandbox`` setting: a bool, or text read like a ``*_SANDBOX`` variable."""
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE
    return bool(value)


_C = TypeVar("_C")


def config_of(config_class: type[_C], config: _C | Mapping[str, Any]) -> _C:
    """``config`` itself, or a ``config_class`` built from a mapping of its arguments."""
    if isinstance(config, config_class):
        return config
    return config_class(**config)  # type: ignore[arg-type]


_INT = re.compile(r"[+-]?[0-9]+")


def env_int(env: EnvSource, key: str) -> int | None:
    """An integer variable, or ``None`` when unset or not an integer."""
    value = (env.get(key) or "").strip()
    return int(value) if _INT.fullmatch(value) else None


def require_setting(gateway: str, key: str, value: object) -> str:
    """The value, or raises a :class:`ConfigurationError` naming ``key``."""
    if not isinstance(value, str) or value.strip() == "":
        raise ConfigurationError(gateway, key)
    return value


def optional_setting(value: object) -> str | None:
    """An optional string, ``None`` when blank."""
    return value if isinstance(value, str) and value.strip() != "" else None


def trim_url(url: str) -> str:
    """Removes trailing slashes."""
    return url.rstrip("/")
