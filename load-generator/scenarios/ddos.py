"""
scenarios/ddos.py
=================
Controlled educational flood. NOT a real attack — this is a load-test
scenario that targets an instance you own.

What changed vs the original
----------------------------
1) A LoadTestShape now drives the load so the run has a clear, readable
   profile on the dashboard:

       warm-up  → BURST → plateau (saturation) → drop (recovery) → end

   This gives you, in a single run, the "before / during / after"
   needed to show: saturation, SLO violation, and — crucially — the
   RECOVERY phase (objective #9). The launcher's -u / -r are ignored
   while a shape is active; only -t (duration) still matters, so set
   the duration in the control panel to >= the shape total (85s here).

2) Near-zero think time keeps request-rate pressure high.

3) catch_response marks 5xx / connection failures as Locust failures,
   so the *load-generator's own* report is honest too (not just the
   server dashboard). Useful to cross-check detectability (objective #8).

Note on 5xx visibility
----------------------
mpm_prefork does NOT emit 503 on worker exhaustion — it queues. To make
the server actually return 5xx (and light up error rate / the donut /
the error taxonomy), enable the load-shedding gate in api/_prepend.php
by setting NEXUS_MAX_INFLIGHT on the web container. Without that gate
this scenario shows latency + CPU saturation but error rate stays ~0%.

Run this only against infrastructure you have permission to test.
"""

from locust import HttpUser, LoadTestShape, between, task


class DDoSUser(HttpUser):
    """
    Minimal user: no shop journey, just hammer the home page as fast as
    the (tiny) think time allows. Inherits HttpUser directly (NOT
    ShopUser) so we never send POST checkouts during the flood.
    """
    # Very small think time: aggressive, but not a literal zero-wait
    # busy loop (that's what `saturation` is for).
    wait_time = between(0.01, 0.05)

    @task
    def flood_home(self):
        with self.client.get(
            "/index.php",                 # era "/" — endpoint troppo leggero
            name="GET /index.php (flood)",
            catch_response=True,
         ) as r:
            if r.status_code >= 500:
                 r.failure(f"server overloaded: HTTP {r.status_code}")
            elif r.status_code == 429:
                 r.failure("rate limited: HTTP 429")


class DDoSShape(LoadTestShape):
    """
    Five-phase profile (each tuple: cumulative_end_s, target_users, spawn_rate):

        0–8s   : warm-up at 50 users          → baseline reference on the charts
        8–18s  : BURST to 1200 users @ 300/s  → the attack ramp
        18–60s : hold 1200 users              → saturation / SLO violation window
        60–85s : drop to 15 users @ 100/s     → RECOVERY observation window
        >85s   : end the test

    Total ~85s, so the control panel's default 90s duration completes the
    whole profile (including recovery). Bump users to 1500–2000 if your
    client machine and target can take more; lower to 600–800 if the
    *load generator itself* becomes the bottleneck (watch the Locust host's
    own CPU — if it's pegged, you're measuring the client, not the server).
    """
    stages = [
        (8,   40,   40),
        (18,  200,  50),    # era 400 @ 200/s — troppo per il client su /index.php
        (60,  200,  1),
        (85,  15,   80),
    ]

    def tick(self):
        run_time = self.get_run_time()
        for end_time, users, spawn_rate in self.stages:
            if run_time < end_time:
                return (users, spawn_rate)
        return None  # signals "test complete"
