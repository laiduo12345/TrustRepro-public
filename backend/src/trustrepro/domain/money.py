"""All product amounts are CNY; foreign catalog prices are conversion inputs only."""

from decimal import Decimal

CURRENCY = "CNY"
DEFAULT_USD_TO_CNY = Decimal("6.7")


def from_usd(value, rate=DEFAULT_USD_TO_CNY):
    return Decimal(str(value)) * Decimal(str(rate))
