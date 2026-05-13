"""
Container Crash and Recovery — a service dies, Docker restarts it, MTTR measured.

Phases:
  0–15%  : pre-incident baseline
  15%    : fastapi container killed -> instant 5xx storm
  15–40% : downtime, requests fail, health checks failing
  40%    : Docker auto-restart kicks in
  40–60% : warm-up phase (container booting, accepting first connections)
  60–100%: back to healthy

Demonstrates: MTTR (mean time to recovery), the value of orchestration
auto-restart, and how observability lets us measure it.

Run:  python3 scenarios/container_crash.py [duration_seconds]
"""

import sys
import random
import time
from metrics_writer import (
    ScenarioRunner, load_metrics, save_metrics, update_kpis,
    write_log, jitter, DEFAULT_SERVICES,
)


def main(duration=90):
    runner = ScenarioRunner("container_crash", duration=duration)
    runner.start()

    crashed_at  = None
    restarted_at = None
    recovered_at = None
    crash_logged = False
    restart_logged = False
    recovery_logged = False

    while runner.is_running():
        p = runner.progress()

        if p < 0.15:
            phase = "pre_crash"
        elif p < 0.40:
            phase = "down"
            if crashed_at is None:
                crashed_at = time.time()
        elif p < 0.60:
            phase = "warming_up"
            if restarted_at is None:
                restarted_at = time.time()
        else:
            phase = "healthy"
            if recovered_at is None:
                recovered_at = time.time()

        if phase == "pre_crash":
            rps      = jitter(40, 0.10)
            p50, p95, p99 = jitter(45, 0.1), jitter(120, 0.1), jitter(180, 0.1)
            err_rate = jitter(0.3, 0.5)
            avail    = 99.95
            cpu, mem = jitter(28, 0.1), jitter(45, 0.05)
            queue    = round(jitter(3, 0.4))
            db_lat   = jitter(12, 0.2)
            fastapi_status, fastapi_err = "healthy", 0.3
        elif phase == "down":
            # Nginx absorbs traffic but cannot route — all 5xx.
            rps      = jitter(40, 0.10)
            p50      = jitter(15, 0.1)  # fail fast — no upstream
            p95      = jitter(35, 0.1)
            p99      = jitter(80, 0.1)
            err_rate = jitter(98, 0.02)
            avail    = jitter(0, 0.0)
            cpu      = jitter(12, 0.1)  # nothing's running
            mem      = jitter(35, 0.05)
            queue    = round(jitter(45, 0.15))  # client retries pile up
            db_lat   = jitter(8, 0.1)
            fastapi_status, fastapi_err = "down", 100.0
        elif phase == "warming_up":
            # Container is up but cache cold, slow first requests.
            rps      = jitter(35, 0.15)
            p50      = jitter(180, 0.15)
            p95      = jitter(600, 0.15)
            p99      = jitter(1400, 0.15)
            err_rate = jitter(8, 0.20)
            avail    = jitter(85, 0.02)
            cpu      = jitter(75, 0.10)  # warm-up burns CPU
            mem      = jitter(55, 0.05)
            queue    = round(jitter(18, 0.20))
            db_lat   = jitter(22, 0.15)
            fastapi_status, fastapi_err = "warning", 4.0
        else:  # healthy
            rps      = jitter(42, 0.10)
            p50, p95, p99 = jitter(45, 0.1), jitter(120, 0.1), jitter(180, 0.1)
            err_rate = jitter(0.4, 0.5)
            avail    = jitter(99.92, 0.001)
            cpu, mem = jitter(30, 0.1), jitter(46, 0.05)
            queue    = round(jitter(4, 0.4))
            db_lat   = jitter(12, 0.2)
            fastapi_status, fastapi_err = "healthy", 0.3

        services = {name: {"status": "healthy",
                           "latency": round(jitter(20, 0.3)),
                           "error_rate": round(jitter(0.3, 0.5), 2),
                           "uptime": round(avail, 2)}
                    for name in DEFAULT_SERVICES}
        services["fastapi"] = {
            "status": fastapi_status,
            "latency": round(jitter(20, 0.3)) if fastapi_status == "healthy" else 0,
            "error_rate": fastapi_err,
            "uptime": round(avail, 2),
        }

        metrics = load_metrics()
        update_kpis(metrics, rps, p50, p95, p99, err_rate, avail,
                    cpu, mem, queue, db_lat, services)
        save_metrics(metrics)
        runner.record_peak(rps, p99, err_rate)

        # Phase transition logs (fire once each).
        if phase == "down" and not crash_logged:
            write_log("CRITICAL", "system", "fastapi container exited (SIGKILL) — exit code 137")
            write_log("ERROR",    "nginx",  "upstream fastapi:8000 unreachable")
            write_log("WARN",     "system", "health check failed — 1/3")
            crash_logged = True
        if phase == "down" and random.random() < 0.4:
            write_log("ERROR", "nginx", "502 Bad Gateway returned to client")

        if phase == "warming_up" and not restart_logged:
            write_log("INFO", "system",  "docker: restarting fastapi (policy=on-failure)")
            write_log("INFO", "fastapi", "starting up — loading models, warming cache")
            restart_logged = True
        if phase == "warming_up" and random.random() < 0.25:
            write_log("WARN", "fastapi", f"slow first request — cold cache ({round(p95)}ms)")

        if phase == "healthy" and not recovery_logged:
            write_log("INFO", "fastapi", "health check passing — service ready")
            mttr = round(recovered_at - crashed_at) if (recovered_at and crashed_at) else None
            write_log("INFO", "system", f"recovery complete — MTTR {mttr}s")
            recovery_logged = True

        runner.sleep()

    mttr = round(recovered_at - crashed_at) if (recovered_at and crashed_at) else None
    runner.finish(
        slo_met=False,
        recovery_time=mttr,
        notes=f"Container crash recovered via Docker auto-restart. MTTR {mttr}s.",
    )


if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    main(duration)
