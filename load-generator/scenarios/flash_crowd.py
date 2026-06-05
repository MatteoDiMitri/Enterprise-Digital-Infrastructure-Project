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
    Steep spike held at the Pi's SERVE-ABLE edge (test phase):
      0–3s  : short baseline at 60 users
      3–5s  : near-vertical wall to 350 users at 200/s
      5–90s : hold at 350 — CPU pinned red, ~120-150 rps SUSTAINED, queue building
      >90s  : end the test

    Why 350 and not thousands: the dashboard measures *served* PHP work. On a
    4-core Pi the practical ceiling is ~120-150 rps served and ~150 requests
    in-flight (Apache mpm_prefork MaxRequestWorkers default). Pushing the user
    count far higher only helps if the LOAD BOX can actually hold that many open
    sockets. On Linux/macOS the cap is `ulimit -n` (default 1024 / 256). On
    WINDOWS there is no ulimit: the equivalent walls are ephemeral-port /
    TIME_WAIT exhaustion (WinError 10048 / 10055) and single-process gevent
    limits. Past the client's ceiling it chokes, stops sending, and served
    throughput COLLAPSES while the Pi sits near-idle (the 1200-user run gave
    11 rps / 0% CPU — that was the client, not the Pi). Practical fix: you do
    NOT need thousands. 350-450 users already maxes a 4-core Pi and is well
    within a single Windows Locust process. For genuinely high counts on
    Windows, run Locust from WSL2 (where ulimit applies) or distributed
    (master + workers) so connections spread across processes.

    Each tuple: (cumulative_end_time_seconds, target_users, spawn_rate)
    """
    stages = [
        (5,  60,   30),     # 0–5s : baseline, connections warm up gently
        (20, 350,  40),     # 5–20s: ramp to 350 at 40/s (fast but no cold-connect storm)
        (90, 350,  1),      # 20–90s: HOLD at 350 — read the dashboard HERE
    ]
    # --- Alternatives (swap the block above) -------------------------------
    # Push the edge (expect the first real 5xx + queue spikes; ~450 is the
    # collapse threshold on a single Pi — do NOT go past ~500 or throughput
    # implodes like the 1200 run did):
    #   (3, 80, 80), (5, 450, 220), (90, 450, 1)
    #
    # Classic viral PULSE (spike, crush, recover — very legible story):
    #   (5, 50, 25), (8, 380, 170), (40, 380, 1), (65, 70, 15), (90, 70, 1)

    def tick(self):
        run_time = self.get_run_time()
        for end_time, users, spawn_rate in self.stages:
            if run_time < end_time:
                return (users, spawn_rate)
        return None    # signals "test complete"
