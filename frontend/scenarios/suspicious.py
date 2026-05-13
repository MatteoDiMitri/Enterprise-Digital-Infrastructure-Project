"""
Suspicious Requests / Rate Limiting — attack traffic detected and blocked.

Demonstrates how the system handles malicious traffic:
  - SQL injection attempts hit /products and /orders
  - Rate limiter detects abnormal request frequency from a few IPs
  - Those IPs get 429 Too Many Requests responses
  - Legitimate traffic is mostly preserved
  - Error rate climbs only briefly, then 4xx (rate-limit) responses dominate

Distinctive signature:
  - 4xx errors spike (not 5xx)
  - latency stays normal (we reject fast)
  - availability stays high
  - "blocked IPs" counter visible in logs

Run:  python3 scenarios/suspicious.py [duration_seconds]
"""

import sys
import random
import time
from metrics_writer import (
    ScenarioRunner, load_metrics, save_metrics, update_kpis,
    write_log, jitter, DEFAULT_SERVICES,
)

ATTACK_IPS = ["203.0.113.42", "198.51.100.17", "192.0.2.88"]
SQLI_PATTERNS = [
    "' OR 1=1--",
    "; DROP TABLE users--",
    "UNION SELECT password FROM users",
    "admin'--",
    "' OR 'a'='a",
]


def main(duration=90):
    runner = ScenarioRunner("suspicious", duration=duration)
    runner.start()
    write_log("WARN", "nginx", "anomalous request pattern detected — entering rate-limit mode")

    blocked_count = 0

    while runner.is_running():
        p = runner.progress()

        if p < 0.10:
            attack_mult, phase = 0.0, "pre_attack"
        elif p < 0.20:
            attack_mult = (p - 0.10) / 0.10
            phase = "attack_starting"
        elif p < 0.80:
            attack_mult = jitter(1.0, 0.10)
            phase = "rate_limited"
        else:
            attack_mult = max(0.0, 1.0 - (p - 0.80) / 0.20)
            phase = "attack_subsiding"

        # Total RPS climbs because attackers add traffic on top of legit users.
        rps      = jitter(40 + 80 * attack_mult, 0.10)
        # Latency stays low — we reject malicious requests fast.
        p50      = jitter(45, 0.10)
        p95      = jitter(125 + 30 * attack_mult, 0.10)
        p99      = jitter(200 + 60 * attack_mult, 0.12)
        # Errors are mostly 4xx (rate-limit) not 5xx — handled separately below.
        err_rate = jitter(0.5 + 18 * attack_mult, 0.10)
        avail    = jitter(99.90 - 0.3 * attack_mult, 0.002)
        cpu      = jitter(30 + 12 * attack_mult, 0.08)
        mem      = jitter(46 + 4 * attack_mult, 0.05)
        queue    = round(jitter(4 + 8 * attack_mult, 0.20))
        db_lat   = jitter(13, 0.15)

        services = {name: {"status": "healthy",
                           "latency": round(jitter(20, 0.3)),
                           "error_rate": round(jitter(0.3, 0.5), 2),
                           "uptime": round(avail, 2)}
                    for name in DEFAULT_SERVICES}
        if attack_mult > 0.5:
            services["nginx"]["status"] = "warning"

        metrics = load_metrics()
        update_kpis(metrics, rps, p50, p95, p99, err_rate, avail,
                    cpu, mem, queue, db_lat, services)

        # Override the status distribution so we see 4xx spike, not 5xx.
        total_reqs = max(round(rps), 1)
        rate_limited = round(total_reqs * 0.25 * attack_mult)
        s5xx = round(total_reqs * 0.005)
        s4xx = round(total_reqs * 0.02) + rate_limited
        s3xx = round(total_reqs * 0.03)
        s2xx = max(0, total_reqs - s5xx - s4xx - s3xx)
        metrics["status_distribution"] = {"2xx": s2xx, "3xx": s3xx, "4xx": s4xx, "5xx": s5xx}

        save_metrics(metrics)
        runner.record_peak(rps, p99, err_rate)

        # Attack-specific log lines.
        if phase == "attack_starting" and random.random() < 0.5:
            ip   = random.choice(ATTACK_IPS)
            sqli = random.choice(SQLI_PATTERNS)
            write_log("WARN", "nginx",
                      f'suspicious payload from {ip} on /products?id={sqli}')
        if phase == "rate_limited" and random.random() < 0.45:
            ip = random.choice(ATTACK_IPS)
            templates = [
                ("WARN",  "nginx", f"429 Too Many Requests for {ip} — limit exceeded"),
                ("ERROR", "nginx", f"WAF rule SQLI-001 matched — blocked {ip}"),
                ("WARN",  "nginx", f"rate limit triggered for {ip} ({random.randint(150, 400)} req/s)"),
                ("WARN",  "fastapi", f"rejected malformed query string from {ip}"),
            ]
            write_log(*random.choice(templates))
            blocked_count += 1
        if phase == "attack_subsiding" and random.random() < 0.25:
            write_log("INFO", "nginx", "attack traffic subsiding — rate limits lifting")

        runner.sleep()

    runner.finish(
        slo_met=True,  # legitimate traffic was preserved
        notes=f"Suspicious traffic blocked — {blocked_count} rate-limit events, no SLO breach.",
    )


if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    main(duration)
