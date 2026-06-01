"""
scenarios/ddos.py
=================
Controlled educational flood. NOT a real attack — this is a load-test
scenario that targets the network/server layer of an instance you own.

What it is NOT
--------------
- No malicious payloads.
- No traffic amplification.
- No source spoofing.
- No persistent connection abuse.

It is just many concurrent virtual users sending plain GETs as fast as
their think time allows. The point is to measure how the front layer
(nginx, PHP-FPM, OS socket buffers) holds up under request-rate pressure.

Run this only against infrastructure you have permission to test.
"""

from locust import HttpUser, between, task


class DDoSUser(HttpUser):
    """
    Minimal user: no journey, just hammer the home page.

    - Tiny think time (50–200ms) keeps the request rate per user high
      without going completely to zero (zero think time is what
      `saturation` is for).
    - Inherits HttpUser directly (NOT ShopUser) so we don't accidentally
      send POST checkouts during the flood.
    """
    wait_time = between(0.05, 0.2)

    @task
    def flood_home(self):
        self.client.get("/", name="GET / (flood)")
