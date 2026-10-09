"""
HOOD Demo App - Test Suite
Verifies user profile, discount calculation, and tax calculation.
"""

import pytest
from demo_app.app_service import get_user_profile, calculate_discount, calculate_tax


def test_user_profile_lookup():
    user = get_user_profile(1)
    assert user["name"] == "Alice"
    assert user["tier"] == 1


def test_calculate_tax():
    # Regular tax 10%
    assert calculate_tax(100.0, is_vip=False) == 10.0
    # VIP tax 5%
    assert calculate_tax(100.0, is_vip=True) == 5.0


def test_calculate_discount_tier_1():
    # 100 * 1 / 100 = 1.0
    assert calculate_discount(100.0, tier=1) == 1.0


def test_calculate_discount_tier_0():
    # Tier 0 should receive 0.0 discount without raising an exception
    discount = calculate_discount(100.0, tier=0)
    assert discount == 0.0
