"""The exact money amount type."""

from __future__ import annotations

import re
from decimal import Decimal

from ._errors import InvalidPaymentDataError

__all__ = ["Amount", "AmountInput"]

_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)?")


class Amount:
    """An exact, non-negative money amount, kept as decimal text.

    It never passes through a float, so it is never rounded. Build one with
    :meth:`Amount.kyat` for whole amounts, :meth:`Amount.parse` for decimal text or
    :meth:`Amount.of` for any accepted input. Each gateway then checks it against
    its documented rules (Wave Money, AYA and Yoma MMQR only accept whole kyat,
    KBZ Pay up to 2 decimal places, CyberSource any).

    Amounts are immutable and ``str(amount)`` is the exact text sent to the
    gateway, e.g. ``"1000.50"``.
    """

    __slots__ = ("_value",)

    _value: str

    def __init__(self, amount: AmountInput) -> None:
        """The same as :meth:`Amount.of`: ``Amount(1000)``, ``Amount("1000.50")``."""
        object.__setattr__(self, "_value", Amount.of(amount)._value)

    @classmethod
    def _make(cls, value: str) -> Amount:
        instance = object.__new__(cls)
        object.__setattr__(instance, "_value", value)
        return instance

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Amount is immutable")

    @classmethod
    def kyat(cls, amount: int) -> Amount:
        """A whole amount, e.g. ``Amount.kyat(1000)`` for 1000 MMK.

        Works for whole units of any currency. Floats, booleans and negative
        values raise an :class:`InvalidPaymentDataError` for the ``amount`` field.
        """
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise _invalid(
                "The amount field must be a whole number of kyat; use "
                "Amount.parse('10.50') for decimal amounts, "
                f"got {amount!r}."
            )
        if amount < 0:
            raise _invalid("The amount field must not be negative.")
        return cls._make(str(amount))

    @classmethod
    def parse(cls, amount: str) -> Amount:
        """A decimal amount written as plain digits, e.g. ``Amount.parse("1000.50")``.

        Signs, exponents, spaces and thousands separators are rejected with an
        :class:`InvalidPaymentDataError`. Leading zeros of the whole part are
        removed (``"007.50"`` becomes ``"7.50"``); the fractional digits are kept
        exactly as given.
        """
        if not isinstance(amount, str) or _PATTERN.fullmatch(amount) is None:
            raise _invalid(
                f"The amount field must be a number such as 1000 or 1000.50, got {amount!r}."
            )
        whole, dot, fraction = amount.partition(".")
        whole = whole.lstrip("0") or "0"
        return cls._make(f"{whole}.{fraction}" if dot else whole)

    @classmethod
    def of(cls, amount: AmountInput) -> Amount:
        """Any accepted amount input as an :class:`Amount`.

        Takes an ``Amount`` (returned as is), a whole ``int`` (as
        :meth:`kyat`), decimal text (as :meth:`parse`) or a finite, non-negative
        :class:`~decimal.Decimal` (its exact digits, e.g. ``Decimal("1000.50")``
        is ``"1000.50"``). Floats are never accepted.
        """
        if isinstance(amount, Amount):
            return amount
        if isinstance(amount, str):
            return cls.parse(amount)
        if isinstance(amount, Decimal):
            if not amount.is_finite():
                raise _invalid(f"The amount field must be a finite number, got {amount!r}.")
            if amount < 0:
                raise _invalid("The amount field must not be negative.")
            return cls.parse(format(amount.copy_abs(), "f"))
        return cls.kyat(amount)

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Amount('{self._value}')"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Amount):
            return _normalize(self._value) == _normalize(other._value)
        return NotImplemented

    def __hash__(self) -> int:
        return hash(_normalize(self._value))

    def decimal_places(self) -> int:
        """The number of fractional digits, e.g. ``2`` for ``1000.50``."""
        _, dot, fraction = self._value.partition(".")
        return len(fraction) if dot else 0

    def whole_part(self) -> str:
        """The digits before the decimal point, e.g. ``"1000"`` for ``1000.50``."""
        return self._value.partition(".")[0]

    def is_zero(self) -> bool:
        """Whether the amount equals zero, e.g. ``0`` or ``0.00``."""
        return self._value.replace(".", "").strip("0") == ""

    def is_positive(self) -> bool:
        """Whether the amount is greater than zero."""
        return not self.is_zero()

    def to_decimal(self) -> Decimal:
        """The amount as an exact :class:`~decimal.Decimal`, e.g. ``Decimal("1000.50")``."""
        return Decimal(self._value)

    def equals(self, other: Amount | str | None) -> bool:
        """Whether both amounts have the same value, ignoring trailing fractional zeros.

        ``1000``, ``1000.0`` and ``1000.00`` are equal. ``None`` is never equal,
        so a gateway amount that was not sent never matches. Text is compared as
        given, e.g. ``callback.amount``.
        """
        if other is None:
            return False
        text = other._value if isinstance(other, Amount) else other
        return _normalize(self._value) == _normalize(text)


AmountInput = Amount | int | str | Decimal
"""What payment data accepts as an amount: an :class:`Amount`, a whole ``int``,
decimal text such as ``"1000.50"`` or a :class:`~decimal.Decimal`. Never a float."""


def _normalize(value: str) -> str:
    return value.rstrip("0").rstrip(".") if "." in value else value


def _invalid(message: str) -> InvalidPaymentDataError:
    return InvalidPaymentDataError({"amount": message})


def to_amount(value: object) -> Amount | None:
    """``value`` as an :class:`Amount`, or ``None`` when it is not a valid amount."""
    if isinstance(value, (Amount, str, Decimal)) or (
        isinstance(value, int) and not isinstance(value, bool)
    ):
        try:
            return Amount.of(value)
        except InvalidPaymentDataError:
            return None
    return None
