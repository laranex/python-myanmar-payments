"""Lossless JSON: numbers keep the exact text the gateway sent."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Union

__all__ = [
    "JsonNumber",
    "LosslessObject",
    "LosslessValue",
    "dumps",
    "parse_object",
    "to_plain",
    "to_plain_object",
]


class JsonNumber:
    """A JSON number kept as the exact text the gateway sent.

    ``1000.50`` stays ``1000.50`` and large integers keep every digit. Only used
    internally while verifying signatures and reading values.
    """

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text

    def __eq__(self, other: object) -> bool:
        return isinstance(other, JsonNumber) and other.text == self.text

    def __hash__(self) -> int:
        return hash(self.text)

    def __repr__(self) -> str:
        return f"JsonNumber({self.text!r})"


LosslessValue = Union[
    str, bool, None, JsonNumber, "list[LosslessValue]", "dict[str, LosslessValue]"
]
LosslessObject = dict[str, LosslessValue]


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def parse_object(text: str) -> LosslessObject | None:
    """Parses strict JSON (RFC 8259) without passing numbers through a float.

    Returns the object, or ``None`` when the text is not valid JSON or not an
    object (an array, a string or ``null``).
    """
    try:
        value = json.loads(
            text,
            parse_int=JsonNumber,
            parse_float=JsonNumber,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def to_plain(value: LosslessValue) -> Any:
    """Converts a lossless value to plain Python values for the ``raw`` of results.

    Integers become ``int`` and other numbers an exact :class:`~decimal.Decimal`,
    never a float.
    """
    if isinstance(value, JsonNumber):
        return _number(value.text)
    if isinstance(value, list):
        return [to_plain(item) for item in value]
    if isinstance(value, dict):
        return to_plain_object(value)
    return value


def to_plain_object(value: LosslessObject) -> dict[str, Any]:
    """Converts a lossless object to plain Python values."""
    return {key: to_plain(item) for key, item in value.items()}


def _number(text: str) -> int | Decimal:
    if any(char in text for char in ".eE"):
        return Decimal(text)
    try:
        return int(text)
    except ValueError:  # Beyond Python's int string conversion limit.
        return Decimal(text)


def dumps(value: Any) -> str:
    """Compact JSON like JavaScript's ``JSON.stringify``.

    Decimals and amounts are written as strings, so no float is involved.
    """
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)
