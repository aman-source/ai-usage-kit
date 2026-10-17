import os

import httpx
from sharedlib import Order, parse_order, price
from sharedlib.core import Pricer

BASE = os.getenv("SVC_URL", "http://localhost:8000")


class OrderClient:
    def __init__(self, base_url: str = BASE):
        self.http = httpx.Client(base_url=base_url)
        self.pricer = Pricer()

    def fetch(self, order_id: int) -> Order:
        r = self.http.get(f"/api/v1/orders/{order_id}")
        return parse_order(r.json())

    def create(self, payload: dict) -> dict:
        return httpx.post(f"{BASE}/api/v1/orders", json=payload).json()

    def remove(self, order_id: int) -> None:
        self.http.delete(f"/api/v1/orders/{order_id}")

    def total(self, order: Order) -> float:
        return price(order) + self.pricer.quote(order)


class DiscountPricer(Pricer):
    def quote(self, order: Order) -> float:
        return super().quote(order) * 0.9
