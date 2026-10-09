"""Reads gateway values as the exact text gateways sign."""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal

from ._json import JsonNumber, LosslessObject

__all__ = ["get", "is_nested", "object_at", "optional", "scalar_string", "trimmed"]


def scalar_string(value: object) -> str | None:
    """A scalar as the text a gateway signs.

    Strings as is, numbers exactly as sent, booleans as ``true``/``false``.
    Objects, lists and ``None`` return ``None``.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, JsonNumber):
        return value.text
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() else None
    return None


def is_nested(value: object) -> bool:
    """Whether ``value`` is an object or a list, which no gateway signs."""
    return isinstance(value, (dict, list))


def get(source: Mapping[str, object], key: str) -> str:
    """The value at ``key`` as text, or ``""`` when it is missing or not a scalar."""
    text = scalar_string(source.get(key))
    return "" if text is None else text


def optional(source: Mapping[str, object], key: str) -> str | None:
    """The value at ``key`` as text, or ``None`` when it is missing or not a scalar."""
    return scalar_string(source.get(key))


def trimmed(source: Mapping[str, object], key: str) -> str:
    """The value at ``key`` with surrounding whitespace removed."""
    return get(source, key).strip()


def object_at(source: Mapping[str, object], key: str) -> LosslessObject | None:
    """The value at ``key`` when it is an object."""
    value = source.get(key)
    return value if isinstance(value, dict) else None
