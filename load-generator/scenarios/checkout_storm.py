"""
scenarios/checkout_storm.py
===========================
Many users placing orders almost simultaneously. The goal is to stress
the WRITE path: PHP -> PDO -> MySQL transactions in checkout.php, and to
make the DATABASE the visible bottleneck (DB latency the star, MariaDB the
first component to degrade).

WHAT CHANGED (the "MariaDB stays at 4ms HEALTHY" fix)
-----------------------------------------------------
The contention engine lives in checkout.php: it does
`SELECT id, stock FROM products WHERE id = ? FOR UPDATE` on each cart row,
in ascending id order, so concurrent checkouts SERIALIZE on those locks
instead of deadlocking. That only bites if many checkouts hit the SAME
rows. The base `_random_cart()` samples from the FULL catalog, so two
concurrent orders almost never lock the same product -> no queueing ->
DB latency stays flat (the 4ms / HEALTHY you observed) and the latency
showed up on PHP instead.

Two changes fix it:

  1) `_random_cart()` is overridden to draw carts ONLY from the first
     `HOT_PRODUCTS` rows. Every storm user now contends for the same
     handful of rows -> transactions queue on those locks -> DB query
     latency climbs -> MariaDB crosses its p95 warning threshold FIRST,
     while GET / (non-locking MVCC read) stays healthy. That is the
     POST-vs-GET divergence this scenario is supposed to show.
     task_order itself is reused unchanged (no duplication) -- this only
     changes WHICH products land in the cart.

  2) A hard-coded `CheckoutStormShape` drives the load so the run has a
     readable warm-up -> ramp -> HOLD -> recovery profile, independent of
     the control panel's -u / -r (Locust ignores those while a shape is
     active; only the duration -t still matters -- set it to >= 90s).

Note: checkout.php returns HTTP 200 with {"success":false} on a rolled-back
transaction (lock-wait timeout / db_error), so this scenario manifests as
LATENCY + Locust failures + the nexus_checkout_orders_total{status=failure}
counter -- NOT as 5xx on the donut. That is intentional: 5xx is the ddos
scenario's signature; checkout_storm owns the DB-latency signature.

Tuning
------
- More DB pressure: lower HOT_PRODUCTS to 1, or raise the HOLD user count.
- Less pressure / p99 pinned at innodb_lock_wait_timeout (~50s): raise
  HOT_PRODUCTS or lower users.
- If the LOAD GENERATOR itself pegs (Locust host CPU 100%, served rps
  collapses, DB goes idle) you're measuring the client -> lower users, or
  run Locust from WSL2 / distributed.
"""

import random

from locust import LoadTestShape, between

from _base import ShopUser


class CheckoutStormUser(ShopUser):
    # Rush pacing: users in a buying frenzy don't browse, they retry the
    # order. Tight enough to pile checkouts up, not a zero-wait busy loop.
    wait_time = between(0.2, 0.6)

    # How many product rows ALL storm users fight over. This is the main
    # contention knob: fewer hot rows = sharper DB-lock queueing. 2 gives
    # a strong-but-survivable signature; drop to 1 for maximum contention.
    HOT_PRODUCTS = 2

    # Heavy emphasis on POST /checkout.php, with a little browsing so the
    # pattern looks like a genuine shopping rush rather than a synthetic
    # POST flood.
    tasks = {
        ShopUser.task_browse: 1,
        ShopUser.task_order:  10,
    }

    # ---- Override: concentrate carts on the hot rows ----------------------
    def _random_cart(self, min_items: int = 1, max_items: int = 2) -> dict:
        """
        Same shape as ShopUser._random_cart, but samples ONLY from the
        first HOT_PRODUCTS catalog rows so concurrent checkouts collide on
        the same `SELECT ... FOR UPDATE` locks in checkout.php.
        """
        catalog = type(self).product_catalog
        if not catalog:
            return {"items": []}

        hot = catalog[:self.HOT_PRODUCTS]
        # Never sample more distinct items than the hot set contains.
        k = min(random.randint(min_items, max_items), len(hot))
        chosen = random.sample(hot, k=k)

        return {
            "items": [
                {"id": p["id"], "qty": random.randint(1, 3), "price": p["price"]}
                for p in chosen
            ]
        }


class CheckoutStormShape(LoadTestShape):
    """
    Hard-coded profile (each tuple: cumulative_end_s, target_users, spawn_rate):

        0-5s   : warm-up at 40 users        -> baseline reference on the charts
        5-20s  : ramp to 280 users @ 40/s   -> concurrent checkouts build up
        20-70s : HOLD 280 users             -> READ THE DASHBOARD HERE:
                                               DB latency climbs, MariaDB ->
                                               WARNING, POST p99 >> GET p99
        70-90s : drop to 10 users @ 60/s    -> recovery / drain window

    Total = 90s, so the control panel's default 90s duration completes the
    whole profile. 280 users keeps a single (Windows) Locust process inside
    its socket ceiling while still generating heavy write concurrency on the
    hot rows; the queueing comes from the LOCKS, not from raw user count.
    """
    stages = [
        (5,  40,  40),
        (20, 280, 40),
        (70, 280, 1),
        (90, 10,  60),
    ]

    def tick(self):
        run_time = self.get_run_time()
        for end_time, users, spawn_rate in self.stages:
            if run_time < end_time:
                return (users, spawn_rate)
        return None  # signals "test complete"
