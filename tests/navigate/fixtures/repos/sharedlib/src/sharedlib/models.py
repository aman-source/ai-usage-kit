from dataclasses import dataclass


@dataclass
class Order:
    id: int
    amount: float
    currency: str = "USD"
