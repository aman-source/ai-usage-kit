from fastapi import APIRouter
from sharedlib import Order, parse_order, price

from .util import parse

router = APIRouter(prefix="/v1")


@router.get("/orders/{order_id}")
def get_order(order_id: int):
    order = Order(id=order_id, amount=10.0)
    return {"id": order.id, "total": price(order)}


@router.post("/orders")
def create_order(payload: dict):
    order = parse_order(payload)
    return {"id": order.id, "meta": parse(b"x")}


@router.delete("/orders/{order_id}")
def delete_order(order_id: int):
    return {"deleted": order_id}
