"""
scenarios/degradation.py
========================
Partial-failure scenario. Normal traffic is generated against a backend
that we EXPECT to misbehave — slow responses, occasional errors,
intermittent failures.

What this scenario does
-----------------------
- Generates a normal mix of browse/detail/order traffic.
- Marks any successful response slower than 1500ms as a failure, so
  the Locust report surfaces tail latency that would otherwise be
  hidden inside the "successful 200" bucket.
- Adds a low-weight "broken link" task that requests a path that
  should 404 — useful for verifying error-rate dashboards light up.

What this scenario does NOT do
------------------------------
It does not INJECT degradation server-side. The degradation has to
come from the backend (a deliberately misconfigured DB, throttled
upstream, etc.). This scenario is the *client side* of the experiment.

Purpose
-------
- Surface tail-latency as failures in load-test reports.
- Confirm dashboards/alerts react to 4xx without misclassifying them
  as healthy 200s.
- Test the system's resilience under partial degradation.
"""

import random
from locust import between
from _base import ShopUser


class DegradationUser(ShopUser):
    wait_time = between(1, 5)

    # Treat any 200 slower than 1.5s as a failure for visibility.
    SLOW_THRESHOLD_MS = 1500

    def task_broken_link(self):
        """
        Hit a path we expect to 404. Low weight: simulates a small
        fraction of users hitting stale URLs or deprecated endpoints.
        """
        self.client.get(
            "/this-page-does-not-exist",
            name="GET /this-page-does-not-exist (expected 404)",
        )

    # Mostly normal traffic, with a small spike of bad-URL hits.
    tasks = {
        ShopUser.task_browse:  6,
        ShopUser.task_detail:  3,
        ShopUser.task_order:   1,
        task_broken_link:      1,
    }
