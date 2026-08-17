"""Decimal-backed value objects: Price, Quantity, Money.

All three reject float input unconditionally (binary floats silently lose
precision - unacceptable for order/P&L arithmetic per rules/05-testing.md).
`int` is accepted and losslessly promoted to Decimal. There is no float
conversion API anywhere on these types - not even an explicit one. A
caller holding a float converts it to Decimal itself (e.g. via
`Decimal(str(value))`) before ever reaching the domain; that boundary
conversion belongs to whatever adapter parses the external payload, not to
the domain kernel.

Scale is capped at 6 fractional digits, matching the NUMERIC(20,6) columns
throughout docs/schemas/. A value with more digits is rejected, not
silently rounded - rounding a mis-specified price/quantity would hide a
bug rather than surface it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from atp_domain.errors import InvalidMoneyValueError

MAX_SCALE = 6


def _coerce_decimal(value: object, *, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise InvalidMoneyValueError(f"{field_name} must be a Decimal or int, not bool.")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        raise InvalidMoneyValueError(
            f"{field_name} does not accept float - binary floats lose precision. "
            f"There is no float conversion API on this type; the domain never "
            f"performs this conversion. Convert to Decimal (e.g. via "
            f"Decimal(str(value))) at the adapter boundary, before the value "
            f"reaches the domain."
        )
    raise InvalidMoneyValueError(
        f"{field_name} must be a Decimal or int, got {type(value).__name__}."
    )


def _validate_finite(value: Decimal, *, field_name: str) -> None:
    if value.is_nan():
        raise InvalidMoneyValueError(f"{field_name} must not be NaN.")
    if value.is_infinite():
        raise InvalidMoneyValueError(f"{field_name} must not be infinite.")


def _validate_scale(value: Decimal, *, field_name: str) -> None:
    exponent = value.as_tuple().exponent
    if isinstance(exponent, str):  # "n" (NaN) or "F" (Infinity) - already excluded above
        raise InvalidMoneyValueError(f"{field_name} must be a finite Decimal.")
    if exponent < -MAX_SCALE:
        raise InvalidMoneyValueError(
            f"{field_name} has more than {MAX_SCALE} fractional digits: {value}."
        )


def normalize_decimal(
    value: object, *, field_name: str, allow_zero: bool = True, allow_negative: bool = True
) -> Decimal:
    """Public entry point for other domain modules that need a validated,
    scale-checked Decimal without wrapping it in Price/Quantity/Money -
    e.g. Position's signed share count, which is neither a magnitude
    (Quantity) nor a monetary amount (Money)."""
    return _normalize(
        value, field_name=field_name, allow_zero=allow_zero, allow_negative=allow_negative
    )


def _normalize(
    value: object, *, field_name: str, allow_zero: bool, allow_negative: bool
) -> Decimal:
    decimal_value = _coerce_decimal(value, field_name=field_name)
    _validate_finite(decimal_value, field_name=field_name)
    _validate_scale(decimal_value, field_name=field_name)
    if not allow_negative and decimal_value < 0:
        raise InvalidMoneyValueError(f"{field_name} must not be negative: {decimal_value}.")
    if not allow_zero and decimal_value == 0:
        raise InvalidMoneyValueError(f"{field_name} must not be zero.")
    return decimal_value


@dataclass(frozen=True, slots=True)
class Price:
    """A per-unit price. Always strictly positive - a zero or negative
    price is not a representable domain value."""

    value: Decimal

    def __post_init__(self) -> None:
        normalized = _normalize(
            self.value, field_name="Price", allow_zero=False, allow_negative=False
        )
        object.__setattr__(self, "value", normalized)

    def __mul__(self, quantity: Quantity) -> Money:
        if not isinstance(quantity, Quantity):
            return NotImplemented
        return Money(self.value * quantity.value)

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class Quantity:
    """An order quantity magnitude. Always strictly positive - direction
    is conveyed separately by Side, never by a signed Quantity."""

    value: Decimal

    def __post_init__(self) -> None:
        normalized = _normalize(
            self.value, field_name="Quantity", allow_zero=False, allow_negative=False
        )
        object.__setattr__(self, "value", normalized)

    def __mul__(self, price: Price) -> Money:
        if not isinstance(price, Price):
            return NotImplemented
        return Money(self.value * price.value)

    def is_multiple_of(self, step: Decimal) -> bool:
        if step <= 0:
            raise InvalidMoneyValueError("step must be positive.")
        return (self.value % step) == 0

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class Money:
    """A monetary amount - fees, P&L, cash. Unlike Price/Quantity, may be
    negative (a loss) or zero."""

    value: Decimal

    def __post_init__(self) -> None:
        normalized = _normalize(
            self.value, field_name="Money", allow_zero=True, allow_negative=True
        )
        object.__setattr__(self, "value", normalized)

    @classmethod
    def zero(cls) -> Money:
        return cls(Decimal(0))

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.value + other.value)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.value - other.value)

    def __neg__(self) -> Money:
        return Money(-self.value)

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.value < other.value

    def __le__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.value <= other.value

    def __gt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.value > other.value

    def __ge__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.value >= other.value

    def __str__(self) -> str:
        return str(self.value)
