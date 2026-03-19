"""Deterministic monetary rounding for FreightWatch.

Always use round_monetary() for financial calculations to avoid floating-point errors.
Uses ROUND_HALF_UP (commercial rounding): 0.5 always rounds away from zero.

Example:
    round_monetary(Decimal("2.675"))  # → Decimal("2.68")  ✓
    round_monetary(Decimal("2.665"))  # → Decimal("2.67")  ✓
"""

from decimal import ROUND_HALF_UP, Decimal


def round_monetary(value: Decimal | float | int, places: int = 2) -> Decimal:
    """Round a monetary value using HALF_UP rounding to the given decimal places.

    Args:
        value: The amount to round (Decimal, float, or int).
        places: Number of decimal places (default: 2).

    Returns:
        Rounded Decimal value.

    Raises:
        TypeError: If value cannot be converted to Decimal.
    """
    quantizer = Decimal(10) ** -places
    return Decimal(str(value)).quantize(quantizer, rounding=ROUND_HALF_UP)
