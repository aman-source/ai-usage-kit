from agent_client.client import DiscountPricer, OrderClient
from sharedlib import Order


def test_total(monkeypatch):
    c = OrderClient()
    assert c.total(Order(id=1, amount=1.0)) > 0


def test_discount():
    assert DiscountPricer().quote(Order(id=1, amount=10.0)) > 0
