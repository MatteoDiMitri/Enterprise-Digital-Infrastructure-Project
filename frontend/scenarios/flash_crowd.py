"""
Flash Crowd — sudden traffic spike (10× baseline RPS).

Phases:
  0–10%   : baseline traffic
  10–25%  : sharp ramp up (viral event)
  25–70%  : sustained spike — queue depth climbs, latency degrades
  70–100% : ramp down and recovery

Demonstrates: how the load balancer queue fills before latency spikes,
how P99 explodes while P50 stays moderate, and how SLO is breached.

Run:  python3 scenarios/flash_crowd.py [duration_seconds]
"""

import sys
import random
import time
from metrics_writer import (
    ScenarioRunner, load_metrics, save_metrics, update_kpis,
    write_log, jitter, DEFAULT_SERVICES,
)


def main(duration=90):
    runner = ScenarioRunner("flash_crowd", duration=duration)
    runner.start()
    write_log("WARN", "nginx",
              "traffic anomaly detected — RPS climbing rapidly")
    write_log("INFO", "system",
              "scenario flash_crowd: ramping traffic 1x -> 10x baseline")

    recovery_start = None
    recovery_time  = None

    while runner.is_running():
        p = runner.progress()

        # Smooth multi-phase shape using piecewise scaling.
        if p < 0.10:
            mult = 1.0
            phase = "baseline"
        elif p < 0.25:
            # Ramp from 1x to 10x.
            mult = 1.0 + (p - 0.10) / 0.15 * 9.0
            phase = "ramp_up"
        elif p < 0.70:
            mult = jitter(10.0, 0.08)
            phase = "peak"
        else:
            # Ramp back down to 1x.
            mult = max(1.0, 10.0 - (p - 0.70) / 0.30 * 9.0)
            phase = "recovery"
            if recovery_start is None:
                recovery_start = time.time()

        rps      = jitter(42 * mult, 0.10)
        # Latency rises non-linearly with load (queueing theory).
        load_factor = mult ** 1.4
        p50      = jitter(45  * (1 + (load_factor - 1) * 0.3), 0.10)
        p95      = jitter(120 * (1 + (load_factor - 1) * 0.8), 0.12)
        p99      = jitter(180 * (1 + (load_factor - 1) * 1.5), 0.15)
        err_rate = max(0.3, jitter((mult - 1) * 0.6, 0.20))
        avail    = max(95.0, 99.95 - max(0, mult - 4) * 0.8)
        cpu      = min(98, jitter(28 + (mult - 1) * 7, 0.05))
        mem      = min(95, jitter(45 + (mult - 1) * 3, 0.05))
        queue    = max(0, round(jitter((mult - 1) * 25, 0.15)))
        db_lat   = jitter(12 * (1 + (load_factor - 1) * 0.2), 0.15)

        services = {}
        for name in DEFAULT_SERVICES:
            if name in ("nginx", "fastapi") and mult > 6:
                status = "degraded"
            elif mult > 3:
                status = "warning"
            else:
                status = "healthy"
            services[name] = {
                "status": status,
                "latency": round(jitter(20 * (1 + (mult - 1) * 0.5), 0.2)),
                "error_rate": round(max(0.0, jitter((mult - 1) * 0.4, 0.3)), 2),
                "uptime": round(avail, 2),
            }

        metrics = load_metrics()
        update_kpis(metrics, rps, p50, p95, p99, err_rate, avail,
                    cpu, mem, queue, db_lat, services)
        save_metrics(metrics)
        runner.record_peak(rps, p99, err_rate)

        # Phase-specific log entries.
        if phase == "ramp_up" and random.random() < 0.4:
            write_log("WARN", "nginx",
                      f"upstream queue depth {queue} — approaching limit")
        if phase == "peak" and random.random() < 0.3:
            templates = [
                ("WARN",  "nginx",   f"upstream response time {round(p99)}ms — SLO threshold breached"),
                ("ERROR", "fastapi", "worker pool saturated — request queued"),
                ("WARN",  "fastapi", f"P99 latency {round(p99)}ms exceeds 1000ms target"),
                ("ERROR", "nginx",   "upstream timeout (504) on /products"),
            ]
            write_log(*random.choice(templates))
        if phase == "recovery" and random.random() < 0.2:
            write_log("INFO", "system", f"traffic normalising — RPS down to {round(rps)}")

        # When recovery starts, detect the moment we return to safe SLO.
        if recovery_start is not None and recovery_time is None:
            if p99 < 1000 and err_rate < 2:
                recovery_time = round(time.time() - recovery_start)
                write_log("INFO", "system", f"system recovered — recovery time {recovery_time}s")

        runner.sleep()

    runner.finish(
        slo_met=False,
        recovery_time=recovery_time,
        notes="Flash crowd 10× baseline — queue saturation caused P99 latency breach.",
    )


if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    main(duration)
