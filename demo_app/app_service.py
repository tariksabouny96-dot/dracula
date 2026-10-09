"""
HOOD Demo App - User Profile API & Micro-Service
Intentionally contains a defect for reproduction, root-cause diagnosis, and autonomous fix verification.
Defect: calculate_discount raises ZeroDivisionError when tier is 0, and returns incorrect tax rate for VIP.
"""

from typing import Dict, Any


def get_user_profile(user_id: int) -> Dict[str, Any]:
    users = {
        1: {"id": 1, "name": "Alice", "tier": 1, "is_vip": False},
        2: {"id": 2, "name": "Bob", "tier": 2, "is_vip": True},
        3: {"id": 3, "name": "Charlie", "tier": 0, "is_vip": False}
    }
    if user_id not in users:
        raise KeyError(f"User {user_id} not found")
    return users[user_id]


def calculate_discount(price: float, tier: int) -> float:
    # DEFECT: If tier == 0, division by zero occurs!
    # Expected: tier 0 gets 0.0 discount, otherwise discount = (price * tier) / 100
    if tier == 0:
        # Deliberate bug for reproduction:
        return price / tier
    return (price * tier) / 100.0


def calculate_tax(amount: float, is_vip: bool) -> float:
    # VIPs pay 5% tax, regular pay 10%
    tax_rate = 0.05 if is_vip else 0.10
    return round(amount * tax_rate, 2)
