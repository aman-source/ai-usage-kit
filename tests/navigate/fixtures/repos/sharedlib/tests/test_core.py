from sharedlib import Pricer, parse_order, price


def test_parse_order():
    o = parse_order({"id": 1, "amount": 10})
    assert o.id == 1


def test_price():
    o = parse_order({"id": 1, "amount": 10})
    assert price(o) == 12.0


def test_quote():
    o = parse_order({"id": 1, "amount": 10})
    assert Pricer().quote(o) == 12.0
