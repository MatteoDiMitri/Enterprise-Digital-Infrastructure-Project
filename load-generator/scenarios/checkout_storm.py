"""
scenarios/checkout_storm.py
===========================
Many users placing orders almost simultaneously. The goal is to stress
the WRITE path: PHP → PDO → MySQL transactions in checkout.php.

Behavior
--------
- Inherits the ShopUser journey but rebalances task weights so orders
  dominate (10×) compared to one light browse step for realism.
- Shorter wait time (0.5–2s) so checkouts pile up quickly.
- Reuses task_order from the base class so cart payload generation
  stays consistent with other scenarios.

Purpose
-------
- Stress MySQL transactions (BEGIN / commit / rollback under load).
- Detect lock contention, connection-pool exhaustion, or deadlocks.
- Measure POST-vs-GET latency divergence under pressure.
"""

from locust import between
from _base import ShopUser


class CheckoutStormUser(ShopUser):
    wait_time = between(0.5, 2)

    # Heavy emphasis on POST /checkout.php. We keep a small amount of
    # browsing so the load mirrors a "shopping rush" pattern rather than
    # an obviously synthetic POST flood.
    tasks = {
        ShopUser.task_browse: 1,
        ShopUser.task_order:  10,
    }
