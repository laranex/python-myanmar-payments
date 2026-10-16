"""Validates payment data against each gateway's documented limits."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from decimal import Decimal

from ._amount import Amount, to_amount
from ._errors import InvalidPaymentDataError
from ._values import ascii_trim


@dataclass(frozen=True)
class AmountRule:
    """What a gateway's documentation allows for an amount."""

    gateway: str
    """The gateway's display name, used in error messages."""
    max_decimals: int | None
    """The most fractional digits allowed: 0 means whole amounts only, None no limit."""
    max_length: int | None = None
    """Limits the amount's text length."""
    allow_zero: bool = False
    """Accepts an amount of 0."""


def byte_length(value: str) -> int:
    """UTF-8 byte length, matching the byte limits in the gateway documentation."""
    return len(value.encode("utf-8", errors="surrogatepass"))


def is_int(value: object) -> bool:
    """Whether ``value`` is an ``int`` and not a ``bool``."""
    return isinstance(value, int) and not isinstance(value, bool)


class Validator:
    """Collects one error per field (the first one wins) and raises them together."""

    def __init__(self) -> None:
        self._errors: dict[str, str] = {}

    def required(self, field: str, value: object) -> Validator:
        if not isinstance(value, str) or ascii_trim(value) == "":
            self._fail(field, f"The {field} field is required.")
        return self

    def string(self, field: str, value: object) -> Validator:
        """Fails when the value is set but not a string."""
        if value is not None and not isinstance(value, str):
            self._fail(field, f"The {field} field must be a string.")
        return self

    def length(self, field: str, value: object, minimum: int, maximum: int) -> Validator:
        if isinstance(value, str) and value != "":
            size = byte_length(value)
            if size < minimum or size > maximum:
                self._fail(
                    field,
                    f"The {field} field must be {minimum} characters."
                    if minimum == maximum
                    else f"The {field} field must be between {minimum} and {maximum} characters.",
                )
        return self

    def max(self, field: str, value: object, maximum: int) -> Validator:
        if isinstance(value, str) and byte_length(value) > maximum:
            self._fail(field, f"The {field} field must not be greater than {maximum} characters.")
        return self

    def pattern(
        self, field: str, value: object, pattern: re.Pattern[str], description: str
    ) -> Validator:
        if isinstance(value, str) and value != "" and pattern.fullmatch(value) is None:
            self._fail(field, f"The {field} field may only contain {description}.")
        return self

    def between(self, field: str, value: object, minimum: int, maximum: int) -> Validator:
        """Fails when a set value is not an integer within [minimum, maximum]."""
        if value is not None and (
            not is_int(value) or not minimum <= value <= maximum  # type: ignore[operator]
        ):
            self._fail(field, f"The {field} field must be between {minimum} and {maximum}.")
        return self

    def positive(self, field: str, value: object) -> Validator:
        """Fails when a set value is not an integer greater than 0."""
        if value is not None and (not is_int(value) or value < 1):  # type: ignore[operator]
            self._fail(field, f"The {field} field must be greater than 0.")
        return self

    def amount(self, field: str, value: object, rule: AmountRule) -> Validator:
        if value is None:
            return self._fail(field, f"The {field} field is required.")
        if isinstance(value, float):
            return self._fail(
                field,
                f"The {field} field must not be a float; use Amount.kyat(1000) or "
                "Amount.parse('10.50').",
            )
        if not isinstance(value, (Amount, str, Decimal)) and not is_int(value):
            return self._fail(
                field,
                f"The {field} field must be an Amount, e.g. Amount.kyat(1000) or "
                "Amount.parse('1000.50').",
            )
        if is_int(value) and value < 0:  # type: ignore[operator]
            return self._fail(field, f"The {field} field must not be negative.")
        amount = to_amount(value)
        if amount is None:
            return self._fail(
                field, f"The {field} field must be a non-negative number such as 1000 or 1000.50."
            )
        places = amount.decimal_places()
        if rule.max_decimals == 0 and places > 0:
            return self._fail(
                field,
                f"{rule.gateway} does not accept decimal amounts; "
                f"the {field} field must be a whole number.",
            )
        if rule.max_decimals is not None and rule.max_decimals > 0 and places > rule.max_decimals:
            return self._fail(
                field,
                f"{rule.gateway} accepts at most {rule.max_decimals} decimal places; "
                f"the {field} field has {places}.",
            )
        if not rule.allow_zero and amount.is_zero():
            return self._fail(field, f"The {field} field must be greater than 0.")
        if rule.max_length is not None and len(str(amount)) > rule.max_length:
            return self._fail(
                field, f"The {field} field must not be greater than {rule.max_length} characters."
            )
        return self

    def url(self, field: str, value: object) -> Validator:
        """Fails when a set value is not an absolute http or https URL."""
        if isinstance(value, str) and value != "" and not is_http_url(value):
            self._fail(field, f"The {field} field must be a valid http or https URL.")
        return self

    def when(self, condition: bool, field: str, message: str) -> Validator:
        if condition:
            self._fail(field, message)
        return self

    def failed(self) -> bool:
        """Whether any check failed so far."""
        return bool(self._errors)

    def validate(self) -> None:
        """Raises an :class:`InvalidPaymentDataError` when any check failed."""
        if self._errors:
            raise InvalidPaymentDataError(self._errors)

    def _fail(self, field: str, message: str) -> Validator:
        self._errors.setdefault(field, message)
        return self


_PRINTABLE_ASCII = re.compile(r"[\x21-\x7e]+")
_HTTP_URL = re.compile(
    r"(?i:https?)://"
    r"(?:[^/?#@]*@)?"  # User info.
    r"(?P<host>\[[^\]/?#]*\]|[^:/?#\[\]]*)"
    r"(?::(?P<port>[^/?#]*))?"
    r"(?:[/?#].*)?",
    re.DOTALL,
)
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")


def is_http_url(value: str) -> bool:
    """Whether ``value`` is an http or https URL, checked like PHP's ``FILTER_VALIDATE_URL``.

    Printable ASCII only (no whitespace or control characters); ``http://`` or
    ``https://`` in any case followed by a host: a bracketed IP literal, or
    dot-separated labels of letters, digits and inner ``-`` (1 to 63 characters
    each, at most 253 in all, one trailing dot allowed). A port is 0 to 65535.
    User info, path, query and fragment may hold any printable ASCII.
    """
    if _PRINTABLE_ASCII.fullmatch(value) is None:
        return False
    match = _HTTP_URL.fullmatch(value)
    if match is None:
        return False
    port = match.group("port")
    if port is not None and not (1 <= len(port) <= 5 and port.isdigit() and int(port) <= 65535):
        return False
    host = match.group("host")
    if host.startswith("["):
        return _is_ip_literal(host[1:-1])
    return _is_hostname(host)


def _is_hostname(host: str) -> bool:
    name = host[:-1] if host.endswith(".") else host
    if name == "" or len(name) > 253:
        return False
    return all(len(label) <= 63 and _LABEL.fullmatch(label) for label in name.split("."))


def _is_ip_literal(address: str) -> bool:
    try:
        ipaddress.IPv6Address(address)
    except ValueError:
        return False
    return True
