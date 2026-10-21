"""Shared internals: hashing, time, environment variables and settings."""

from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import os
import re
import secrets
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, TypeVar
from urllib.parse import quote_plus

from ._errors import ConfigurationError
from ._values import ascii_trim

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
    """The first non-empty value among ``keys``, trimmed of ASCII whitespace, or ``""``."""
    for key in keys:
        value = ascii_trim(env.get(key) or "")
        if value:
            return value
    return ""


_C = TypeVar("_C")


def config_of(config_class: type[_C], config: _C | Mapping[str, Any]) -> _C:
    """``config`` itself, or a ``config_class`` built from a mapping of its arguments.

    Keys that are not arguments of ``config_class`` are ignored, so one shared
    settings mapping can configure several gateways.
    """
    if isinstance(config, config_class):
        return config
    parameters = inspect.signature(config_class).parameters
    return config_class(**{key: value for key, value in config.items() if key in parameters})  # type: ignore[union-attr]


_INT = re.compile(r"[+-]?[0-9]+")

MAX_INTEGER = 2147483647
"""The largest whole number a setting may hold (2^31 - 1), the same in every Laranex SDK."""

DEFAULT_TIMEOUT_SECONDS = 30
"""Seconds the default HTTP client waits for a gateway when no timeout is set."""


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and ascii_trim(value) == "")


def _integer(value: object) -> int | None:
    """An int or integer text from -2147483648 to 2147483647, else ``None``."""
    number: int | None = None
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, str) and _INT.fullmatch(ascii_trim(value)):
        number = int(ascii_trim(value))
    if number is None or not -MAX_INTEGER - 1 <= number <= MAX_INTEGER:
        return None
    return number


def require_seconds(gateway: str, key: str, value: object) -> int:
    """A whole number of seconds from 1 to 2147483647, given as an int or integer text.

    Unset or blank raises a :class:`ConfigurationError` naming ``key``; anything
    else that is not a whole number in that range raises it as invalid.
    """
    if _blank(value):
        raise ConfigurationError(gateway, key)
    seconds = _integer(value)
    if seconds is None or seconds <= 0:
        raise ConfigurationError(gateway, key, invalid=True)
    return seconds


def require_integer(gateway: str, key: str, value: object, requirement: str) -> int:
    """A whole number from -2147483648 to 2147483647, given as an int or integer text.

    Unset or blank raises a :class:`ConfigurationError` naming ``key``; anything
    else that is not a whole number in that range raises it as invalid, naming
    ``requirement``.
    """
    if _blank(value):
        raise ConfigurationError(gateway, key)
    number = _integer(value)
    if number is None:
        raise ConfigurationError(gateway, key, invalid=True, requirement=requirement)
    return number


def require_setting(gateway: str, key: str, value: object) -> str:
    """The value as text, or raises a :class:`ConfigurationError` naming ``key``.

    Text and integers are accepted (an integer becomes its decimal text, e.g.
    ``200123`` is ``"200123"``); blank text, floats, booleans and anything else
    count as missing. The value is returned untrimmed.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or ascii_trim(value) == "":
        raise ConfigurationError(gateway, key)
    return value


# The leading digits of expires_in after ASCII whitespace, like PHP's /^\s*([0-9]+)/.
_LEADING_INT = re.compile(r"[ \t\n\r\x0b\x0c]*([0-9]+)")


def token_ttl(expires_in: str) -> int:
    """Seconds to cache an access token: its lifetime minus a minute, at least a minute.

    ``expires_in`` is read by its leading digits; none (or 0) means one hour.
    """
    match = _LEADING_INT.match(expires_in)
    seconds = int(match.group(1)) if match else 0
    return max(60, (seconds if seconds > 0 else 3600) - 60)


def trim_url(url: str) -> str:
    """Removes trailing slashes."""
    return url.rstrip("/")


def read_timeout(gateway: str, value: object) -> int:
    """The HTTP timeout in whole seconds, :data:`DEFAULT_TIMEOUT_SECONDS` when unset.

    Unset or blank falls back to the default; any other value is read like
    :func:`require_seconds` and reported under ``timeout_in_seconds``.
    """
    if _blank(value):
        return DEFAULT_TIMEOUT_SECONDS
    return require_seconds(gateway, "timeout_in_seconds", value)
