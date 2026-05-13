"""
Backend Error Cascade — one service starts failing, errors propagate.

Demonstrates the classic distributed-systems failure mode where a single
downstream service (here, the orders service) begins returning 5xx errors,
upstream services retry, and the failure cascades through the stack.

Phases:
  0–15%  : pre-incident baseline
  15–80% : cascade in progress — error rate climbs, services degrade
  80–100%: gradual recovery as the failing service comes back

Run:  python3 scenarios/error_cascade.py [duration_seconds]
"""

import sys
import random
import time
from metrics_writer import (
    ScenarioRunner, load_metrics, save_metrics, update_kpis,
    write_log, jitter, DEFAULT_SERVICES,
)


def main(duration=90):
    runner = ScenarioRunner("error_cascade", duration=duration)
    runner.start()

    failing_service = "fastapi"  # the source of the cascade
    recovery_start  = None
    recovery_time   = None
    initial_fail_logged = False

    while runner.is_running():
        p = runner.progress()

        if p < 0.15:
            err_mult, phase = 0.0, "pre_incident"
        elif p < 0.30:
            # Errors begin appearing in the orders service.
            err_mult = (p - 0.15) / 0.15
            phase = "onset"
        elif p < 0.80:
            err_mult = jitter(1.0, 0.1)
            phase = "cascade"
        else:
            err_mult = max(0.0, 1.0 - (p - 0.80) / 0.20)
            phase = "recovery"
            if recovery_start is None:
                recovery_start = time.time()

        # RPS stays roughly normal — the issue is reliability, not capacity.
        rps      = jitter(40, 0.10)
        # Failing requests time out, so latency creeps up too (retries).
        p50      = jitter(50  + 200 * err_mult, 0.10)
        p95      = jitter(140 + 500 * err_mult, 0.12)
        p99      = jitter(220 + 1800 * err_mult, 0.15)
        err_rate = max(0.3, jitter(0.5 + 40 * err_mult, 0.15))
        avail    = max(60.0, 99.95 - 38 * err_mult)
        cpu      = jitter(35 + 25 * err_mult, 0.10)  # retry storms eat CPU
        mem      = jitter(50 + 10 * err_mult, 0.05)
        queue    = round(jitter(5 + 20 * err_mult, 0.20))
        db_lat   = jitter(14 + 30 * err_mult, 0.15)

        services = {}
        for name in DEFAULT_SERVICES:
            if name == failing_service:
                if err_mult > 0.7:
                    status, svc_err = "down", 100.0
                elif err_mult > 0.2:
                    status, svc_err = "degraded", 45.0 * err_mult
                else:
                    status, svc_err = "healthy", 0.5
            elif name in ("nginx", "worker") and err_mult > 0.5:
                # Upstream services degrade because they depend on fastapi.
                status, svc_err = "degraded", 15.0 * err_mult
            else:
                status, svc_err = "healthy", round(jitter(0.3, 0.5), 2)
            services[name] = {
                "status": status,
                "latency": round(jitter(20 + 80 * err_mult, 0.2)),
                "error_rate": round(svc_err, 2),
                "uptime": round(avail, 2),
            }

        metrics = load_metrics()
        update_kpis(metrics, rps, p50, p95, p99, err_rate, avail,
                    cpu, mem, queue, db_lat, services)
        save_metrics(metrics)
        runner.record_peak(rps, p99, err_rate)

        if phase == "onset" and not initial_fail_logged:
            write_log("ERROR", "fastapi",
                      "unhandled exception in POST /orders — IntegrityError")
            write_log("WARN", "nginx",
                      "upstream returning 502 errors — increasing retry rate")
            initial_fail_logged = True

        if phase == "cascade" and random.random() < 0.35:
            templates = [
                ("ERROR", "fastapi", "unhandled exception in POST /orders — IntegrityError"),
                ("ERROR", "nginx",   f"upstream connect() failed (111: Connection refused)"),
                ("ERROR", "worker",  "max retries exceeded for downstream call"),
                ("WARN",  "fastapi", "worker pool exhausted — rejecting requests"),
                ("ERROR", "nginx",   "502 Bad Gateway returned to client"),
            ]
            write_log(*random.choice(templates))

        if phase == "recovery" and random.random() < 0.3:
            write_log("INFO", "fastapi", "error rate decreasing — service stabilising")

        if recovery_start is not None and recovery_time is None:
            if err_rate < 2 and avail > 99:
                recovery_time = round(time.time() - recovery_start)
                write_log("INFO", "system",
                          f"cascade contained — recovery time {recovery_time}s")

        runner.sleep()

    runner.finish(
        slo_met=False,
        recovery_time=recovery_time,
        notes="Backend error cascade originated in fastapi — propagated to nginx and worker.",
    )


if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    main(duration)
