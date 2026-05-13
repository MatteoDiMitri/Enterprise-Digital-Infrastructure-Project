"""
Shared mock-data writer used by every scenario script.

Each scenario emits:
  - a snapshot of system metrics  -> mock_data/metrics_current.json
  - one or more log lines         -> mock_data/logs.jsonl  (append-only)
  - per-service health entries    -> embedded in metrics_current.json
  - an entry in the experiments  -> mock_data/experiments.json  (when scenario ends)

The shape of metrics_current.json is intentionally close to what a real
Prometheus + service-health backend would expose, so the frontend code does
not need to change when the mock layer is replaced by a real API.
"""

import json
import os
import time
import random
import math
from datetime import datetime, timezone
from pathlib import Path

# Resolve mock_data relative to the project root, regardless of CWD.
MOCK_DIR = Path(__file__).resolve().parent.parent / "mock_data"
MOCK_DIR.mkdir(exist_ok=True)

METRICS_FILE     = MOCK_DIR / "metrics_current.json"
LOGS_FILE        = MOCK_DIR / "logs.jsonl"
STATE_FILE       = MOCK_DIR / "scenario_state.json"
EXPERIMENTS_FILE = MOCK_DIR / "experiments.json"

# A 60-point rolling history per metric, so the frontend can draw charts
# without needing a separate time-series store. ~90 seconds at 1.5s tick.
HISTORY_LEN = 60

DEFAULT_SERVICES = ["nginx", "fastapi", "postgres", "redis", "worker", "prometheus"]


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path, data):
    """Atomic write so the frontend never reads a half-written file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def init_metrics_state():
    """Return a fresh metrics structure if no file exists yet."""
    return {
        "timestamp": _now_iso(),
        "scenario": "idle",
        "scenario_started_at": None,
        "kpi": {
            "rps": 0,
            "latency_p50": 0,
            "latency_p95": 0,
            "latency_p99": 0,
            "error_rate": 0.0,
            "availability": 100.0,
            "cpu": 0,
            "memory": 0,
            "queue_depth": 0,
            "active_services": f"{len(DEFAULT_SERVICES)}/{len(DEFAULT_SERVICES)}",
            "slo_status": "ok",
        },
        "history": {
            "rps":         [0] * HISTORY_LEN,
            "latency_p50": [0] * HISTORY_LEN,
            "latency_p95": [0] * HISTORY_LEN,
            "latency_p99": [0] * HISTORY_LEN,
            "error_rate":  [0] * HISTORY_LEN,
            "cpu":         [0] * HISTORY_LEN,
            "memory":      [0] * HISTORY_LEN,
            "queue_depth": [0] * HISTORY_LEN,
            "db_latency":  [0] * HISTORY_LEN,
        },
        "status_distribution": {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0},
        "services": {
            name: {"status": "healthy", "latency": 0, "error_rate": 0.0, "uptime": 100.0}
            for name in DEFAULT_SERVICES
        },
        "endpoints": [],
    }


def load_metrics():
    return _read_json(METRICS_FILE, init_metrics_state())


def save_metrics(metrics):
    metrics["timestamp"] = _now_iso()
    _write_json(METRICS_FILE, metrics)


def push_history(metrics, key, value):
    h = metrics["history"].setdefault(key, [0] * HISTORY_LEN)
    h.append(round(value, 2))
    if len(h) > HISTORY_LEN:
        del h[0 : len(h) - HISTORY_LEN]


def write_log(level, service, message):
    """Append a structured log line."""
    entry = {
        "timestamp": _now_iso(),
        "level": level.upper(),
        "service": service,
        "message": message,
    }
    with open(LOGS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    # Keep log file from growing unbounded over a long demo session.
    _truncate_logs(2000)


def _truncate_logs(max_lines):
    try:
        with open(LOGS_FILE) as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            with open(LOGS_FILE, "w") as f:
                f.writelines(lines[-max_lines:])
    except FileNotFoundError:
        pass


# ── State management (which scenario is running) ──

def read_state():
    return _read_json(STATE_FILE, {"active": None, "started_at": None, "pid": None})


def write_state(state):
    _write_json(STATE_FILE, state)


def set_active_scenario(name, pid):
    write_state({"active": name, "started_at": _now_iso(), "pid": pid})


def clear_active_scenario():
    write_state({"active": None, "started_at": None, "pid": None})


# ── Experiments history (the summary table) ──

def append_experiment(record):
    data = _read_json(EXPERIMENTS_FILE, {"runs": []})
    data["runs"].append(record)
    # Keep the last 50 runs.
    data["runs"] = data["runs"][-50:]
    _write_json(EXPERIMENTS_FILE, data)


# ── Helpers used by scenario scripts ──

def jitter(base, pct=0.1):
    """Small random variation around a base value, ±pct."""
    return base * (1 + (random.random() - 0.5) * 2 * pct)


def compute_slo(latency_p99, error_rate, availability):
    """Simple SLO: p99 < 1000ms, error rate < 2%, availability > 99%."""
    if availability < 95 or error_rate > 10 or latency_p99 > 3000:
        return "violation"
    if availability < 99 or error_rate > 2 or latency_p99 > 1000:
        return "at_risk"
    return "ok"


def derive_endpoints(rps, latency_p95, error_rate):
    """Per-endpoint breakdown based on the global numbers."""
    endpoints = [
        {"method": "GET",  "path": "/products",      "share": 0.42, "lat_mult": 0.9, "err_mult": 1.0},
        {"method": "GET",  "path": "/products/{id}", "share": 0.25, "lat_mult": 0.6, "err_mult": 0.5},
        {"method": "POST", "path": "/orders",        "share": 0.15, "lat_mult": 1.6, "err_mult": 2.0},
        {"method": "GET",  "path": "/orders/{id}",   "share": 0.10, "lat_mult": 0.8, "err_mult": 1.0},
        {"method": "GET",  "path": "/health",        "share": 0.08, "lat_mult": 0.1, "err_mult": 0.0},
    ]
    result = []
    for ep in endpoints:
        ep_rps = rps * ep["share"]
        ep_lat = round(latency_p95 * ep["lat_mult"] * jitter(1.0, 0.05))
        ep_err = round(error_rate * ep["err_mult"] * jitter(1.0, 0.1), 2)
        status = "healthy"
        if ep_err > 5:
            status = "degraded"
        elif ep_err > 1.5 or ep_lat > 1500:
            status = "warning"
        result.append({
            "method":     ep["method"],
            "path":       ep["path"],
            "rpm":        round(ep_rps * 60),
            "latency":    ep_lat,
            "p95":        round(ep_lat * 1.6),
            "error_rate": ep_err,
            "status":     status,
        })
    return result


def update_kpis(metrics, rps, p50, p95, p99, err_rate, avail,
                cpu, mem, queue, db_lat, services_state):
    """Single helper every scenario calls each tick."""
    kpi = metrics["kpi"]
    kpi["rps"]              = round(rps)
    kpi["latency_p50"]      = round(p50)
    kpi["latency_p95"]      = round(p95)
    kpi["latency_p99"]      = round(p99)
    kpi["error_rate"]       = round(err_rate, 2)
    kpi["availability"]     = round(avail, 2)
    kpi["cpu"]              = round(cpu)
    kpi["memory"]           = round(mem)
    kpi["queue_depth"]      = round(queue)
    kpi["slo_status"]       = compute_slo(p99, err_rate, avail)

    healthy = sum(1 for s in services_state.values() if s["status"] == "healthy")
    total   = len(services_state)
    kpi["active_services"]  = f"{healthy}/{total}"

    metrics["services"]     = services_state

    push_history(metrics, "rps",         rps)
    push_history(metrics, "latency_p50", p50)
    push_history(metrics, "latency_p95", p95)
    push_history(metrics, "latency_p99", p99)
    push_history(metrics, "error_rate",  err_rate)
    push_history(metrics, "cpu",         cpu)
    push_history(metrics, "memory",      mem)
    push_history(metrics, "queue_depth", queue)
    push_history(metrics, "db_latency",  db_lat)

    # Realistic HTTP status distribution.
    total_reqs = max(rps, 1)
    err_share  = err_rate / 100.0
    s5xx = round(total_reqs * err_share * 0.7)
    s4xx = round(total_reqs * err_share * 0.3) + round(total_reqs * 0.02)
    s3xx = round(total_reqs * 0.03)
    s2xx = max(0, round(total_reqs) - s5xx - s4xx - s3xx)
    metrics["status_distribution"] = {
        "2xx": s2xx, "3xx": s3xx, "4xx": s4xx, "5xx": s5xx
    }

    metrics["endpoints"] = derive_endpoints(rps, p95, err_rate)


# ── Scenario runner skeleton ──

class ScenarioRunner:
    """
    Every scenario script subclasses or just uses this:
      runner = ScenarioRunner("flash_crowd", duration=60)
      runner.start()
      while runner.is_running():
          # update metrics
          runner.tick()
      runner.finish(summary={...})
    """

    def __init__(self, name, duration, tick_interval=1.5):
        self.name          = name
        self.duration      = duration
        self.tick_interval = tick_interval
        self.start_time    = None
        self.peak          = {"rps": 0, "latency_p99": 0, "error_rate": 0.0}

    def start(self):
        self.start_time = time.time()
        metrics = load_metrics()
        metrics["scenario"] = self.name
        metrics["scenario_started_at"] = _now_iso()
        save_metrics(metrics)
        set_active_scenario(self.name, os.getpid())
        write_log("INFO", "system",
                  f'scenario "{self.name}" started (duration {self.duration}s)')

    @property
    def elapsed(self):
        return time.time() - self.start_time if self.start_time else 0

    def is_running(self):
        return self.elapsed < self.duration

    def progress(self):
        """0.0 -> 1.0 throughout the run."""
        return min(1.0, self.elapsed / self.duration)

    def record_peak(self, rps, p99, err):
        self.peak["rps"]         = max(self.peak["rps"], rps)
        self.peak["latency_p99"] = max(self.peak["latency_p99"], p99)
        self.peak["error_rate"]  = max(self.peak["error_rate"], err)

    def sleep(self):
        time.sleep(self.tick_interval)

    def finish(self, slo_met=True, recovery_time=None, notes=""):
        record = {
            "id":              int(time.time()),
            "scenario":        self.name,
            "started_at":      datetime.fromtimestamp(self.start_time, timezone.utc).isoformat(timespec="seconds"),
            "duration_sec":    round(self.elapsed),
            "peak_rps":        round(self.peak["rps"]),
            "peak_latency_p99":round(self.peak["latency_p99"]),
            "peak_error_rate": round(self.peak["error_rate"], 2),
            "recovery_sec":    recovery_time,
            "slo_met":         slo_met,
            "notes":           notes,
        }
        append_experiment(record)
        write_log("INFO", "system",
                  f'scenario "{self.name}" completed in {record["duration_sec"]}s')

        # Reset metrics to idle baseline.
        metrics = load_metrics()
        metrics["scenario"] = "idle"
        metrics["scenario_started_at"] = None
        save_metrics(metrics)
        clear_active_scenario()
