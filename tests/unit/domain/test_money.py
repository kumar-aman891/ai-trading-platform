"""Tests for atp_domain.money - Decimal determinism, rejection rules,
scale/precision, immutability."""

from __future__ import annotations

from decimal import Decimal

import pytest

from atp_domain.errors import InvalidMoneyValueError
from atp_domain.money import MAX_SCALE, Money, Price, Quantity


def test_price_accepts_decimal() -> None:
    assert Price(Decimal("19.99")).value == Decimal("19.99")


def test_price_accepts_int_and_promotes_losslessly() -> None:
    assert Price(10).value == Decimal(10)


def test_price_rejects_float_directly() -> None:
    with pytest.raises(InvalidMoneyValueError, match="does not accept float"):
        Price(19.99)  # type: ignore[arg-type]


def test_price_has_no_float_conversion_api() -> None:
    """There is no escape hatch anywhere on the type - not even an
    explicit, documented one. A caller holding a float must convert it to
    Decimal itself, outside the domain."""
    assert not hasattr(Price, "from_float")
    assert not hasattr(Quantity, "from_float")
    assert not hasattr(Money, "from_float")


def test_price_rejects_zero() -> None:
    with pytest.raises(InvalidMoneyValueError, match="not be zero"):
        Price(Decimal(0))


def test_price_rejects_negative() -> None:
    with pytest.raises(InvalidMoneyValueError, match="not be negative"):
        Price(Decimal("-1"))


def test_price_rejects_nan() -> None:
    with pytest.raises(InvalidMoneyValueError, match="NaN"):
        Price(Decimal("NaN"))


def test_price_rejects_infinity() -> None:
    with pytest.raises(InvalidMoneyValueError, match="infinite"):
        Price(Decimal("Infinity"))


def test_price_rejects_too_many_fractional_digits() -> None:
    with pytest.raises(InvalidMoneyValueError, match="fractional digits"):
        Price(Decimal("1.1234567"))


def test_price_accepts_exactly_six_fractional_digits() -> None:
    assert Price(Decimal("1.123456")).value == Decimal("1.123456")


def test_price_is_immutable() -> None:
    price = Price(Decimal("10"))
    with pytest.raises(AttributeError):
        price.value = Decimal("20")  # type: ignore[misc]


def test_quantity_rejects_zero_and_negative() -> None:
    with pytest.raises(InvalidMoneyValueError):
        Quantity(Decimal(0))
    with pytest.raises(InvalidMoneyValueError):
        Quantity(Decimal(-5))


def test_quantity_is_multiple_of() -> None:
    assert Quantity(Decimal(100)).is_multiple_of(Decimal(25)) is True
    assert Quantity(Decimal(101)).is_multiple_of(Decimal(25)) is False


def test_money_allows_zero_and_negative() -> None:
    assert Money(Decimal(0)).value == 0
    assert Money(Decimal("-100.50")).value == Decimal("-100.50")


def test_money_rejects_bool() -> None:
    with pytest.raises(InvalidMoneyValueError, match="not bool"):
        Money(True)  # type: ignore[arg-type]


def test_money_rejects_wrong_type() -> None:
    with pytest.raises(InvalidMoneyValueError):
        Money("10")  # type: ignore[arg-type]


def test_money_arithmetic_is_deterministic() -> None:
    a = Money(Decimal("10.10"))
    b = Money(Decimal("0.05"))
    assert (a + b).value == Decimal("10.15")
    assert (a - b).value == Decimal("10.05")
    assert (-a).value == Decimal("-10.10")


def test_money_comparisons() -> None:
    assert Money(Decimal(5)) < Money(Decimal(10))
    assert Money(Decimal(10)) <= Money(Decimal(10))
    assert Money(Decimal(10)) > Money(Decimal(5))
    assert Money(Decimal(10)) >= Money(Decimal(10))


def test_quantity_times_price_yields_money() -> None:
    notional = Quantity(Decimal(10)) * Price(Decimal("19.99"))
    assert isinstance(notional, Money)
    assert notional.value == Decimal("199.90")


def test_price_times_quantity_yields_money_and_is_commutative() -> None:
    a = Price(Decimal("19.99")) * Quantity(Decimal(10))
    b = Quantity(Decimal(10)) * Price(Decimal("19.99"))
    assert a.value == b.value


def test_repeated_decimal_arithmetic_is_bit_for_bit_reproducible() -> None:
    """Decimal (unlike float) never accumulates representation drift."""
    total = Money.zero()
    for _ in range(1000):
        total = total + Money(Decimal("0.01"))
    assert total.value == Decimal("10.00")


def test_full_scale_quantity_times_price_stays_within_max_scale() -> None:
    """Regression (Phase 1 Step 12 Phase A): a quantity and a price
    round-tripped through `NUMERIC(20,6)` come back with 6 fractional
    digits each, so their exact product has 12 - more than `Money`
    accepts. Before this was fixed, every real paper execution raised
    `InvalidMoneyValueError` from inside the risk engine rather than
    producing a decision. Unit tests had never caught it because they
    used small-scale literals like `Decimal("10")`."""
    quantity = Quantity(Decimal("10.000000"))
    price = Price(Decimal("100.000000"))

    notional = quantity * price

    assert notional.value == Decimal("1000.000000")
    assert -notional.value.as_tuple().exponent <= MAX_SCALE
    assert (price * quantity).value == notional.value


def test_notional_quantization_rounds_away_from_zero() -> None:
    """Both call sites are risk checks (RISK.LIMIT.001, RISK.CAPITAL.001),
    so quantization must never understate a notional - it may only ever
    overstate it, and so can never admit an order the exact value would
    have rejected."""
    quantity = Quantity(Decimal("0.000001"))
    price = Price(Decimal("1.500000"))

    # Exact product is 0.0000015 (7 dp); rounding away from zero gives
    # 0.000002, never 0.000001.
    assert (quantity * price).value == Decimal("0.000002")
