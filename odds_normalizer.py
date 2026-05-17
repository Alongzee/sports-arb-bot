"""
odds_normalizer.py – canonical odds conversion utilities.
"""

from __future__ import annotations

from decimal import Decimal


def D(value) -> Decimal:
    return Decimal(str(value))


def normalize_decimal(odds) -> Decimal:
    """
    Convert any numeric odds into Decimal safely.
    """

    return D(odds)


def american_to_decimal(odds: int) -> Decimal:
    """
    Convert American odds to decimal odds.

    Example:
        +150 -> 2.50
        -200 -> 1.50
    """

    if odds > 0:
        return D("1") + (
            D(odds) / D("100")
        )

    return D("1") + (
        D("100") / abs(D(odds))
    )


def fractional_to_decimal(
    numerator: int,
    denominator: int,
) -> Decimal:
    """
    Convert fractional odds to decimal odds.

    Example:
        3/2 -> 2.50
        1/1 -> 2.00
    """

    return D("1") + (
        D(numerator) / D(denominator)
    )


def implied_probability(
    decimal_odds: Decimal,
) -> Decimal:
    """
    Convert decimal odds into implied probability.

    Example:
        2.00 -> 0.50
    """

    if decimal_odds <= 0:
        raise ValueError(
            "Decimal odds must be positive"
        )

    return D("1") / decimal_odds


if __name__ == "__main__":

    assert american_to_decimal(150) == D("2.5")
    assert american_to_decimal(-200) == D("1.5")

    assert fractional_to_decimal(3, 2) == D("2.5")

    assert implied_probability(D("2.0")) == D("0.5")

    print("odds_normalizer.py tests passed.")
