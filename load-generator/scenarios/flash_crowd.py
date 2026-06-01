"""
scenarios/flash_crowd.py
========================
Simulates a sudden viral event: traffic ramps from ~100 to ~2000 users
in a short time window, then plateaus.

Behavior
--------
- Same realistic journey as NormalUser.
- Slightly shorter wait time (0.5–2s) — visitors during a viral event
  click faster, less considered browsing.
- A `LoadTestShape` overrides the launcher's `-u` / `-r` flags so the
  RAMP itself is the scenario.

Purpose
-------
Observe how the system scales: where do latency percentiles spike?
Do connection pools / worker queues saturate? Does the autoscaler
(if any) react in time?

Note
----
When a LoadTestShape is present, Locust ignores the CLI `-u` and `-r`
values for the duration of the shape. The launcher still passes them
for consistency, but they don't drive the ramp here.
"""

from locust import LoadTestShape, between
from _base import ShopUser


class FlashCrowdUser(ShopUser):
    """Faster pacing than NormalUser — viral-event visitors click sooner."""
    wait_time = between(0.5, 2)

    # Same task mix as normal traffic; the ramp itself (see shape below)
    # is what makes this scenario distinct.
    tasks = {
        ShopUser.task_browse: 6,
        ShopUser.task_detail: 3,
        ShopUser.task_order:  1,
    }


class FlashCrowdShape(LoadTestShape):
    """
    Three-stage ramp:
      0–10s  : warm-up at 100 users
      10–25s : burst to 2000 users at 200 users/s spawn rate
      25–90s : hold at 2000 users
      >90s   : end the test

    Each tuple: (cumulative_end_time_seconds, target_users, spawn_rate)
    """
    stages = [
        (10, 100,  50),
        (25, 2000, 200),
        (90, 2000, 1),
    ]

    def tick(self):
        run_time = self.get_run_time()
        for end_time, users, spawn_rate in self.stages:
            if run_time < end_time:
                return (users, spawn_rate)
        return None    # signals "test complete"
