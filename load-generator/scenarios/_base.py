"""
scenarios/_base.py
==================
Shared building blocks for every Locust scenario in this project.

Why a single base class?
------------------------
- One source of truth for the simulated PHP-shop endpoints and the product
  catalog. If the backend's URL scheme changes, we patch it here only.
- Lets each scenario stay small: scenarios only override `wait_time`,
  task weights, or attach a `LoadTestShape`. The HTTP journey itself is
  reused.

Simulated journey
-----------------
GET  /                          → home page
GET  /index.php                 → product list / browse
GET  /index.php?product_id=N    → product detail
POST /checkout.php              → place an order (JSON cart payload)

Extending
---------
To add a new scenario:
  1. Create `scenarios/<my_scenario>.py`.
  2. Subclass `ShopUser` (or `HttpUser` directly for non-shop traffic).
  3. Override `wait_time` and/or `tasks` to shape the behavior.
  4. Add the scenario key to the SCENARIOS list in control_panel.html
     and to the ALLOWED_SCENARIOS set in launcher/main.py.
"""

import random
from locust import HttpUser, between


# -----------------------------------------------------------------------------
# Simulated product catalog. Each (id, price) pair matches what the existing
# checkout.php endpoint expects in the cart payload. Prices stay client-side
# for the demo; in production checkout.php recomputes them server-side anyway
# (see the original PHP — totals are recalculated to never trust the client).
# -----------------------------------------------------------------------------
PRODUCT_CATALOG = [
    {"id": 1, "price": 19.99},
    {"id": 2, "price": 29.50},
    {"id": 3, "price": 49.00},
    {"id": 4, "price":  9.90},
    {"id": 5, "price": 14.50},
    {"id": 6, "price": 79.00},
    {"id": 7, "price": 124.99},
    {"id": 8, "price":  5.00},
]


def random_cart(min_items: int = 1, max_items: int = 4) -> dict:
    """Build a plausible cart payload for POST /checkout.php."""
    chosen = random.sample(PRODUCT_CATALOG, k=random.randint(min_items, max_items))
    return {
        "items": [
            {"id": p["id"], "qty": random.randint(1, 3), "price": p["price"]}
            for p in chosen
        ]
    }


# =============================================================================
# Base user class
# =============================================================================
class ShopUser(HttpUser):
    """
    Realistic shop visitor — ABSTRACT base.

    This class only DEFINES the journey steps (task_browse, task_detail,
    task_order). It does NOT declare `tasks` itself, because Locust's
    metaclass MERGES an inherited `tasks` dict with subclass tasks —
    which would mean every concrete scenario double-counts whatever it
    inherits. So instead: each concrete scenario subclass sets its own
    `tasks` dict referencing `ShopUser.task_browse` / `task_detail` /
    `task_order` directly.

    `abstract = True` tells Locust not to instantiate this class even
    if it gets picked up by file discovery.

    Wait time defaults to 1–5s per the project requirement. Scenarios
    that need different pacing (DDoS, Saturation) override `wait_time`.

    `SLOW_THRESHOLD_MS` (opt-in): when set, any successful request that
    takes longer than this many milliseconds is marked as a failure.
    Used by the Degradation scenario so the report surfaces tail
    latency, not just HTTP errors.
    """
    abstract = True              # Locust won't run this class directly
    wait_time = between(1, 5)
    SLOW_THRESHOLD_MS = None     # disabled by default

    # ---- Lifecycle --------------------------------------------------------

    def on_start(self):
        """Every simulated user lands on the home page first."""
        self.client.get("/", name="GET /")

    # ---- Journey steps (referenced by `tasks` dict below) -----------------

    def task_browse(self):
        """Most common action: load the product list."""
        with self.client.get(
            "/index.php",
            name="GET /index.php (browse)",
            catch_response=True,
        ) as r:
            self._mark_slow_if_needed(r)

    def task_detail(self):
        """Open a single product page."""
        prod = random.choice(PRODUCT_CATALOG)
        with self.client.get(
            f"/index.php?product_id={prod['id']}",
            name="GET /index.php?product_id=[id]",   # template name keeps stats grouped
            catch_response=True,
        ) as r:
            self._mark_slow_if_needed(r)

    def task_order(self):
        """Lowest-frequency action: post a cart to /checkout.php."""
        payload = random_cart()
        with self.client.post(
            "/checkout.php",
            json=payload,
            name="POST /checkout.php",
            catch_response=True,
        ) as r:
            # The PHP endpoint always returns 200, even on logical errors —
            # so the JSON body is what tells us whether the order succeeded.
            if r.status_code != 200:
                r.failure(f"checkout returned HTTP {r.status_code}")
                return
            try:
                data = r.json()
            except ValueError:
                r.failure("checkout did not return JSON")
                return
            if not data.get("success"):
                r.failure(f"checkout error: {data.get('error','unknown')}")
                return
            self._mark_slow_if_needed(r)

    # ---- No default `tasks` here on purpose --------------------------------
    # Concrete scenario classes declare their own `tasks` dict (see normal.py,
    # flash_crowd.py, checkout_storm.py, etc.). If we set one here, every
    # subclass would silently *merge* its dict with ours.

    # ---- Internals --------------------------------------------------------

    def _mark_slow_if_needed(self, response):
        """When SLOW_THRESHOLD_MS is set, treat slow 200s as failures."""
        if self.SLOW_THRESHOLD_MS is None:
            return
        # `request_meta` is populated by Locust on every catch_response request.
        elapsed_ms = response.request_meta.get("response_time", 0)
        if elapsed_ms > self.SLOW_THRESHOLD_MS:
            response.failure(
                f"slow response: {int(elapsed_ms)}ms > {self.SLOW_THRESHOLD_MS}ms"
            )
