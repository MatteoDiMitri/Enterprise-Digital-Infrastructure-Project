"""
Baseline Load — simulates normal, healthy system operation.

This is the "before picture" against which every other scenario is compared.
RPS oscillates around ~40 with small noise, latency stays well under SLO,
error rate is near zero, all services are healthy.

Run:  python3 scenarios/baseline.py [duration_seconds]
"""

import sys
import random
from metrics_writer import (
    ScenarioRunner, load_metrics, save_metrics, update_kpis,
    write_log, jitter, DEFAULT_SERVICES,
)


def main(duration=120):
    runner = ScenarioRunner("baseline", duration=duration)
    runner.start()
    write_log("INFO", "nginx", "baseline traffic generator started")

    log_counter = 0

    while runner.is_running():
        # Steady traffic with small organic variation.
        rps      = jitter(42, 0.15)
        p50      = jitter(45,  0.10)
        p95      = jitter(120, 0.12)
        p99      = jitter(180, 0.15)
        err_rate = max(0.0, jitter(0.3, 0.5))
        avail    = jitter(99.95, 0.0005)
        cpu      = jitter(28, 0.10)
        mem      = jitter(45, 0.05)
        queue    = max(0, round(jitter(3, 0.4)))
        db_lat   = jitter(12, 0.20)

        services = {name: {"status": "healthy",
                           "latency": round(jitter(20, 0.3)),
                           "error_rate": round(max(0.0, jitter(0.2, 0.5)), 2),
                           "uptime": 100.0}
                    for name in DEFAULT_SERVICES}

        metrics = load_metrics()
        update_kpis(metrics, rps, p50, p95, p99, err_rate, avail,
                    cpu, mem, queue, db_lat, services)
        save_metrics(metrics)
        runner.record_peak(rps, p99, err_rate)

        log_counter += 1
        # Occasional INFO log lines to make the live log feed feel alive.
        if log_counter % 3 == 0:
            samples = [
                ("INFO", "nginx",   f"GET /products 200 — {round(p95)}ms"),
                ("INFO", "fastapi", f"POST /orders 201 — order #{random.randint(1000,9999)} created"),
                ("INFO", "postgres",f"SELECT products ({round(db_lat)}ms) — {random.randint(8,20)} rows"),
                ("INFO", "nginx",   "GET /health 200 — 1ms"),
                ("INFO", "prometheus", "scrape /metrics 200 — 4ms"),
            ]
            choice = random.choice(samples)
            write_log(*choice)

        runner.sleep()

    runner.finish(slo_met=True,
                  notes="Steady-state baseline — system within all SLO thresholds.")


if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    main(duration)
