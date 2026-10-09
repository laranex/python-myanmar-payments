from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from python_myanmar_payments import Amount, InvalidPaymentDataError


class TestAmount:
    def test_builds_whole_amounts(self) -> None:
        assert str(Amount.kyat(1000)) == "1000"
        assert str(Amount.kyat(0)) == "0"
        assert str(Amount.kyat(10**30)) == "1" + "0" * 30

    @pytest.mark.parametrize("value", [10.5, 1000.0, True, "1000", None])
    def test_rejects_anything_but_an_int_for_kyat(self, value: Any) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            Amount.kyat(value)
        assert "whole number of kyat" in info.value.errors["amount"]

    def test_rejects_negative_amounts(self) -> None:
        with pytest.raises(InvalidPaymentDataError, match="must not be negative"):
            Amount.kyat(-1)
        with pytest.raises(InvalidPaymentDataError, match="must not be negative"):
            Amount.of(Decimal("-0.5"))

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1000", "1000"),
            ("1000.50", "1000.50"),
            ("007.50", "7.50"),
            ("000", "0"),
            ("0.00", "0.00"),
            ("12345678901234567890.123", "12345678901234567890.123"),
        ],
    )
    def test_parses_plain_digits_and_drops_leading_zeros(self, text: str, expected: str) -> None:
        assert str(Amount.parse(text)) == expected

    @pytest.mark.parametrize(
        "text", ["", "-1", "+1", "1e3", "1,000", " 1", "1.", ".5", "1.2.3", "١٢", "1\n", 1000]
    )
    def test_rejects_anything_but_plain_digits(self, text: Any) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            Amount.parse(text)
        assert "such as 1000 or 1000.50" in info.value.errors["amount"]

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1000, "1000"),
            ("0010.10", "10.10"),
            (Decimal("1000.50"), "1000.50"),
            (Decimal("1E+3"), "1000"),
            (Decimal("0.010"), "0.010"),
            (Decimal("-0"), "0"),
        ],
    )
    def test_converts_every_accepted_input(self, value: Any, expected: str) -> None:
        assert str(Amount.of(value)) == expected
        assert str(Amount(value)) == expected

    def test_returns_an_amount_unchanged(self) -> None:
        amount = Amount.kyat(5)
        assert Amount.of(amount) is amount
        assert Amount(amount) == amount

    @pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("sNaN")])
    def test_rejects_non_finite_decimals(self, value: Decimal) -> None:
        with pytest.raises(InvalidPaymentDataError, match="finite number"):
            Amount.of(value)

    def test_rejects_floats(self) -> None:
        with pytest.raises(InvalidPaymentDataError):
            Amount.of(10.5)  # type: ignore[arg-type]

    def test_describes_itself(self) -> None:
        amount = Amount.parse("1000.50")
        assert amount.decimal_places() == 2
        assert Amount.kyat(1000).decimal_places() == 0
        assert amount.whole_part() == "1000"
        assert amount.to_decimal() == Decimal("1000.50")
        assert repr(amount) == "Amount('1000.50')"
        assert Amount.parse("0.00").is_zero()
        assert not Amount.parse("0.01").is_zero()
        assert Amount.parse("0.01").is_positive()
        assert not Amount.kyat(0).is_positive()

    def test_compares_values_ignoring_trailing_fractional_zeros(self) -> None:
        assert Amount.kyat(1000) == Amount.parse("1000.00")
        assert hash(Amount.kyat(1000)) == hash(Amount.parse("1000.0"))
        assert Amount.kyat(1000) != Amount.kyat(100)
        assert Amount.kyat(1000) != "1000"
        assert Amount.kyat(1000).equals("1000.00")
        assert Amount.parse("1000.50").equals(Amount.parse("1000.5"))
        assert not Amount.kyat(10).equals("100")
        assert not Amount.kyat(10).equals(None)
        assert Amount.kyat(100).equals("100")

    def test_compares_by_value_ignoring_leading_zeros(self) -> None:
        assert Amount.kyat(1000) == Amount.parse("01000.000")
        assert hash(Amount.kyat(1000)) == hash(Amount.parse("001000.00"))
        assert Amount.kyat(1000).equals("01000")
        assert Amount.kyat(0).equals("000.00")
        assert Amount.parse("0.50").equals("00.5")
        for text in ("1,000", " 1000", "1000 ", "", "+1000", "1e3", "\u0661\u0660\u0660\u0660"):
            assert not Amount.kyat(1000).equals(text)
        assert not Amount.kyat(1000).equals(1000)  # type: ignore[arg-type]

    def test_quotes_the_rejected_text_in_the_parse_error(self) -> None:
        with pytest.raises(InvalidPaymentDataError) as info:
            Amount.parse("1,000")
        assert info.value.errors["amount"] == (
            'The amount field must be a number such as 1000 or 1000.50, got "1,000".'
        )
        with pytest.raises(InvalidPaymentDataError) as info:
            Amount.parse(1000)  # type: ignore[arg-type]
        assert info.value.errors["amount"].endswith("got 1000.")

    def test_is_immutable(self) -> None:
        amount = Amount.kyat(1)
        with pytest.raises(AttributeError):
            amount._value = "2"
