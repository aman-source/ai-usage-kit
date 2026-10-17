from __future__ import annotations

from .models import Order

TAX_RATE = 0.2


def parse_order(raw: dict) -> Order:
    return Order(id=int(raw["id"]), amount=float(raw["amount"]), currency=raw.get("currency", "USD"))


def price(order: Order, tax: float = TAX_RATE) -> float:
    return round(order.amount * (1 + tax), 2)


class Pricer:
    def __init__(self, tax: float = TAX_RATE):
        self.tax = tax

    def quote(self, order: Order) -> float:
        return self._round(price(order, self.tax))

    def _round(self, value: float) -> float:
        return round(value, 2)

    def bulk(self, orders: list[Order]) -> list[float]:
        return [self.quote(o) for o in orders]
