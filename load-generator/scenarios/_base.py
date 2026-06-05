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
GET  /                              → home page
GET  /api/products.php              → dynamic catalog fetch (once per class)
GET  /index.php                     → product list / browse
GET  /index.php?product_id=N        → product detail
POST /checkout.php                  → place an order (JSON cart payload)

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
    
    # Class-level variable shared across ALL instances.
    # We fetch this once per test run, not once per user, to save DB load.
    product_catalog = []

    # ---- Lifecycle --------------------------------------------------------

    def on_start(self):
        """Every simulated user lands on the home page first and checks the catalog."""
        self.client.get("/", name="GET /")
        
        # If the catalog is empty (first user spawning), fetch it from the DB via API
        if not type(self).product_catalog:
            with self.client.get("/api/products.php", name="Fetch Catalog API", catch_response=True) as response:
                if response.status_code == 200:
                    try:
                        type(self).product_catalog = response.json()
                    except ValueError:
                        response.failure("Catalog API did not return valid JSON")
                else:
                    response.failure(f"Failed to fetch catalog (HTTP {response.status_code})")

    # ---- Cart Helper ------------------------------------------------------

    def _random_cart(self, min_items: int = 1, max_items: int = 4) -> dict:
        """Build a plausible cart payload for POST /checkout.php."""
        catalog = type(self).product_catalog
        if not catalog:
            return {"items": []}
            
        # Safeguard: don't try to sample more items than exist in the database
        k = min(random.randint(min_items, max_items), len(catalog))
        chosen = random.sample(catalog, k=k)
        
        return {
            "items": [
                {"id": p["id"], "qty": random.randint(1, 3), "price": p["price"]}
                for p in chosen
            ]
        }

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
        catalog = type(self).product_catalog
        if not catalog:
            return # Skip if catalog failed to load
            
        prod = random.choice(catalog)
        with self.client.get(
            f"/index.php?product_id={prod['id']}",
            name="GET /index.php?product_id=[id]",   # template name keeps stats grouped
            catch_response=True,
        ) as r:
            self._mark_slow_if_needed(r)

    def task_order(self):
        """Lowest-frequency action: post a cart to /checkout.php."""
        payload = self._random_cart()
        
        if not payload["items"]:
            return # Skip if no items available
            
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