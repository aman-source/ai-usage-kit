from agent_client.client import OrderClient, DiscountPricer


def main() -> None:
    c = OrderClient()
    order = c.fetch(1)
    print(c.total(order), DiscountPricer().quote(order))
