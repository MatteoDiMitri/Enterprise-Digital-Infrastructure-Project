"""
scenarios/normal.py
===================
Baseline scenario. Steady, realistic traffic for measuring the
"healthy" behavior of the system before any stress is applied.

Behavior
--------
- Human-like think time (1–5s) between actions.
- Default weighting: mostly browsing, occasional purchases.
- Moderate load (driven by -u and -r from the launcher).

Purpose
-------
Establish a baseline: latency percentiles, throughput, error rate
under normal conditions. Every other scenario is interpreted against
this baseline.
"""

from _base import ShopUser


class NormalUser(ShopUser):
    """
    Default weighting: browse-heavy with occasional purchases — the
    canonical "healthy traffic" profile.
    """
    tasks = {
        ShopUser.task_browse: 6,
        ShopUser.task_detail: 3,
        ShopUser.task_order:  1,
    }
