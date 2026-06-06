#!/usr/bin/env python3
"""
scenario_cpu_load.py
====================
Generic, scenario-aware CPU load generator for the NEXUS lab.

Watches the active Locust scenario and, while a matching scenario is running,
burns a controllable amount of CPU so the dashboard's `nexus_system_cpu_percent`
shows the target resource-saturation signature. Stops automatically the moment 
the scenario ends.

Tuning (env vars)
-----------------
  NEXUS_BURN_SCENARIO   scenario name that triggers the load    (default: ddos)
  NEXUS_BURN_WORKERS    busy processes / cores to load          (default: all cores)
  NEXUS_BURN_DUTY       busy fraction per worker, 0.0-1.0       (default: 0.85)
  NEXUS_BURN_RAMP_S     seconds to stagger workers on start     (default: 5)
  NEXUS_POLL_S          scenario poll interval, seconds         (default: 1.0)
  NEXUS_SCENARIO_FILE   active-scenario JSON path (file mode)   (default: unset -> HTTP)
  NEXUS_SCENARIO_URL    scenario endpoint (HTTP mode)           (default: http://localhost/api/scenario.php)

Stops cleanly on Ctrl-C / SIGTERM.
"""

import json
import multiprocessing as mp
import os
import signal
import sys
import time
import urllib.request


# ---- Configuration (env-driven) --------------------------------------------

TARGET_SCENARIO = os.getenv("NEXUS_BURN_SCENARIO", "").strip()
WORKERS         = int(os.getenv("NEXUS_BURN_WORKERS", str(os.cpu_count() or 1)))
DUTY            = max(0.0, min(1.0, float(os.getenv("NEXUS_BURN_DUTY", "0.85"))))
RAMP_S          = max(0.0, float(os.getenv("NEXUS_BURN_RAMP_S", "5")))
POLL_S          = max(0.2, float(os.getenv("NEXUS_POLL_S", "1.0")))

SCEN_FILE = os.getenv("NEXUS_SCENARIO_FILE")  # if set -> file mode
SCEN_URL  = os.getenv("NEXUS_SCENARIO_URL", "http://localhost/api/scenario.php")

CONTROL_PERIOD = 0.1   # duty-cycle window per worker (100 ms)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---- CPU worker -------------------------------------------------------------

def _burn(duty: float, stop_evt) -> None:
    busy = duty * CONTROL_PERIOD
    idle = CONTROL_PERIOD - busy
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    while not stop_evt.is_set():
        t_end = time.perf_counter() + busy
        x = 1.000001
        while time.perf_counter() < t_end:
            for _ in range(20000):
                x = x * 1.0000001 + 1.0
        if idle > 0:
            time.sleep(idle)


# ---- Load lifecycle ---------------------------------------------------------

class CpuLoad:
    def __init__(self):
        self.stop_evt = None
        self.procs = []

    @property
    def running(self) -> bool:
        return bool(self.procs)

    def start(self) -> None:
        if self.running:
            return
        self.stop_evt = mp.Event()
        per_worker_delay = (RAMP_S / WORKERS) if (RAMP_S > 0 and WORKERS > 0) else 0.0
        
        # FIX: Dynamic logging that respects the target scenario
        log(f"Target '{TARGET_SCENARIO}' active -> starting CPU load: {WORKERS} worker(s) @ duty {DUTY:.2f} "
            f"(ramp {RAMP_S:.0f}s)")
            
        for i in range(WORKERS):
            p = mp.Process(target=_burn, args=(DUTY, self.stop_evt), daemon=True)
            p.start()
            self.procs.append(p)
            if per_worker_delay:
                time.sleep(per_worker_delay)

    def stop(self) -> None:
        if not self.running:
            return
        log("scenario ended -> stopping CPU load")
        if self.stop_evt is not None:
            self.stop_evt.set()
        for p in self.procs:
            p.join(timeout=2.0)
            if p.is_alive():
                p.terminate()
        self.procs = []
        self.stop_evt = None


# ---- Scenario detection -----------------------------------------------------

def read_active_scenario() -> str:
    if SCEN_FILE:
        try:
            with open(SCEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return (str(data.get("scenario", "idle")) or "idle").strip()
        except FileNotFoundError:
            return "idle"
        except Exception:
            return "idle"
    try:
        with urllib.request.urlopen(SCEN_URL, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (str(data.get("scenario", "idle")) or "idle").strip()
    except Exception:
        return "idle"


# ---- Main loop --------------------------------------------------------------

def main() -> int:
    load = CpuLoad()
    stop = {"flag": False}

    def _shutdown(signum, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    src = f"file:{SCEN_FILE}" if SCEN_FILE else f"http:{SCEN_URL}"
    log(f"watcher up | trigger='{TARGET_SCENARIO}' | source={src} | "
        f"workers={WORKERS} duty={DUTY:.2f} poll={POLL_S:.1f}s")

    last = None
    try:
        while not stop["flag"]:
            scn = read_active_scenario()
            if scn != last:
                log(f"active scenario: {scn}")
                last = scn
            if scn == TARGET_SCENARIO and not load.running:
                load.start()
            elif scn != TARGET_SCENARIO and load.running:
                load.stop()
            time.sleep(POLL_S)
    finally:
        load.stop()
        log("watcher down")
    return 0


if __name__ == "__main__":
    sys.exit(main())