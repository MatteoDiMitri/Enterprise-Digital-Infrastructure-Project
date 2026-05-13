"""
Reset System — clears active scenario state and returns to clean baseline.

Unlike the other scripts, this one is short-lived. It:
  - kills any running scenario process (via the state file)
  - resets metrics_current.json to a healthy idle state
  - appends a "reset" event to the log
  - does NOT touch experiments.json (we want to keep history)

Run:  python3 scenarios/reset.py
"""

import os
import signal
import time
from metrics_writer import (
    load_metrics, save_metrics, init_metrics_state, write_log,
    read_state, clear_active_scenario, update_kpis, DEFAULT_SERVICES, jitter,
)


def main():
    state = read_state()
    if state.get("pid"):
        try:
            os.kill(state["pid"], signal.SIGTERM)
            write_log("INFO", "system",
                      f'reset: terminated scenario "{state.get("active")}" (pid {state["pid"]})')
            time.sleep(0.5)  # let the child clean up
        except ProcessLookupError:
            # Already dead — nothing to do.
            pass
        except PermissionError:
            write_log("WARN", "system",
                      f'reset: could not signal pid {state["pid"]} — clearing state anyway')

    # Reset state.
    clear_active_scenario()

    # Reset live metrics to a healthy baseline snapshot.
    fresh = init_metrics_state()
    services = {name: {"status": "healthy", "latency": round(jitter(20, 0.2)),
                       "error_rate": 0.3, "uptime": 100.0}
                for name in DEFAULT_SERVICES}
    update_kpis(fresh,
                rps=42, p50=45, p95=120, p99=180,
                err_rate=0.3, avail=99.95,
                cpu=28, mem=45, queue=2, db_lat=12,
                services_state=services)
    fresh["scenario"] = "idle"
    save_metrics(fresh)

    write_log("INFO", "system", "system reset complete — all services healthy")


if __name__ == "__main__":
    main()
