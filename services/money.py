"""Opérations monétaires décimales pour les montants présentés à l'utilisateur."""
from decimal import Decimal, ROUND_HALF_UP


def decimal_amount(value):
    return Decimal(str(value))


def rounded_money(value):
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
