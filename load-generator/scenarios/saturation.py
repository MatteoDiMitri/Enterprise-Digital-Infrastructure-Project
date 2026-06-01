"""
scenarios/saturation.py
=======================
Maximum-throughput scenario. Push the server until it breaks.

Behavior
--------
- Near-zero think time (0–50ms).
- Full journey (browse / detail / order) preserved, so we stress the
  whole stack, not just one endpoint.
- POSTs reduced relative to GETs to avoid the test being purely
  database-bound (use `checkout_storm` for that targeted profile).

Purpose
-------
Find the capacity ceiling: at what RPS does latency cross the SLO,
does the error rate climb, or does a downstream component fall over?
This is the "RPS-discovery" scenario.
"""

from locust import between
from _base import ShopUser


class SaturationUser(ShopUser):
    # As fast as the network/CPU will allow; tiny floor avoids a true
    # zero-wait busy loop on the simulated clients themselves.
    wait_time = between(0, 0.05)

    # Read-heavy: many GETs, fewer writes. To stress the write path
    # explicitly, use the checkout_storm scenario instead.
    tasks = {
        ShopUser.task_browse: 8,
        ShopUser.task_detail: 4,
        ShopUser.task_order:  1,
    }
