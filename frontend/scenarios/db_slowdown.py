"""
Database Slowdown — DB query latency climbs, dragging the whole stack with it.

Simulates the effect of an unindexed query, missing cache, or DB lock
contention. Demonstrates how a single downstream component (the database)
can poison every upstream service that depends on it.

Distinctive signature:
  - DB query latency is the leading indicator (climbs first)
  - P99 latency on /orders and /products climbs in lockstep
  - /health endpoint stays fast (doesn't touch the DB)
  - error rate stays low initially, then climbs when timeouts trigger
  - cache hit rate drops, connection pool fills

Run:  python3 scenarios/db_slowdown.py [duration_seconds]
"""

import sys
import random
import time
from metrics_writer import (
    ScenarioRunner, load_metrics, save_metrics, update_kpis,
    write_log, jitter, DEFAULT_SERVICES,
)


def main(duration=120):
    runner = ScenarioRunner("db_slowdown", duration=duration)
    runner.start()
    write_log("INFO", "system", "scenario db_slowdown — injecting heavy unindexed query load")

    recovery_start = None
    recovery_time  = None

    while runner.is_running():
        p = runner.progress()

        if p < 0.10:
            slow, phase = 0.0, "pre_incident"
        elif p < 0.35:
            slow = (p - 0.10) / 0.25  # ramp to peak slowdown
            phase = "degrading"
        elif p < 0.80:
            slow = jitter(1.0, 0.08)
            phase = "saturated"
        else:
            slow = max(0.0, 1.0 - (p - 0.80) / 0.20)
            phase = "recovery"
            if recovery_start is None:
                recovery_start = time.time()

        # DB latency goes from 12ms baseline to ~800ms at peak.
        db_lat = jitter(12 + 800 * slow, 0.12)

        rps   = jitter(40, 0.10)
        p50   = jitter(45  + 350 * slow, 0.10)
        p95   = jitter(140 + 1500 * slow, 0.12)
        p99   = jitter(220 + 5000 * slow, 0.15)
        # Errors only show up at high saturation (timeouts).
        err_rate = max(0.3, jitter(0.5 + 4 * max(0, slow - 0.5), 0.2))
        avail    = max(95.0, 99.95 - 4 * max(0, slow - 0.5))
        cpu      = jitter(30 + 15 * slow, 0.08)
        mem      = jitter(48 + 20 * slow, 0.05)
        queue    = round(jitter(4 + 35 * slow, 0.15))

        services = {}
        for name in DEFAULT_SERVICES:
            if name == "postgres":
                if slow > 0.7:
                    status, svc_err = "degraded", 2.0
                elif slow > 0.3:
                    status, svc_err = "warning", 0.5
                else:
                    status, svc_err = "healthy", 0.1
            elif name in ("fastapi", "worker") and slow > 0.5:
                # Services that depend on the DB degrade with it.
                status, svc_err = "warning", round(0.5 + 2 * slow, 2)
            else:
                status, svc_err = "healthy", round(jitter(0.3, 0.4), 2)
            services[name] = {
                "status": status,
                "latency": round(jitter(20 + 80 * slow, 0.2)),
                "error_rate": svc_err,
                "uptime": round(avail, 2),
            }

        metrics = load_metrics()
        update_kpis(metrics, rps, p50, p95, p99, err_rate, avail,
                    cpu, mem, queue, db_lat, services)
        save_metrics(metrics)
        runner.record_peak(rps, p99, err_rate)

        if phase == "degrading" and random.random() < 0.3:
            write_log("WARN", "postgres",
                      f"slow query detected: SELECT products ({round(db_lat)}ms)")
        if phase == "saturated" and random.random() < 0.35:
            templates = [
                ("WARN",  "postgres", f"connection pool 95% — {round(jitter(48, 0.05))}/50"),
                ("ERROR", "postgres", "deadlock detected — transaction rolled back"),
                ("WARN",  "fastapi",  f"DB call exceeded timeout threshold ({round(db_lat)}ms)"),
                ("WARN",  "postgres", "missing index on orders(user_id)"),
            ]
            write_log(*random.choice(templates))
        if phase == "recovery" and random.random() < 0.25:
            write_log("INFO", "postgres", "query times returning to normal")

        if recovery_start is not None and recovery_time is None:
            if db_lat < 100 and p99 < 1000:
                recovery_time = round(time.time() - recovery_start)
                write_log("INFO", "system", f"DB recovered — recovery time {recovery_time}s")

        runner.sleep()

    runner.finish(
        slo_met=False,
        recovery_time=recovery_time,
        notes="Database slowdown — DB query latency ~800ms caused upstream P99 breach.",
    )


if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    main(duration)
