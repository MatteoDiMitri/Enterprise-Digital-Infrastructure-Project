#!/usr/bin/env python3
"""
ddos_cpu_load.py
================
Scenario-aware CPU load generator for the NEXUS lab (runs on the Raspberry Pi).

Watches the active Locust scenario and, while the DDoS scenario is running,
burns a controllable amount of CPU so the dashboard's `nexus_system_cpu_percent`
shows the resource-saturation signature the ddos scenario is meant to produce.
Stops automatically the moment the scenario ends.

This is a controlled, educational load tool for infrastructure you OWN -- the
same spirit as the project's "controlled test" scenarios. It talks to nothing
external; it only spins local busy-loops and reads the active-scenario flag.

Why it works
------------
`nexus_system_cpu_percent` is computed in system_metrics.php from /proc/stat,
which inside a container reports HOST-wide CPU. So burning CPU here -- on the
Pi host OR in any container -- raises that metric. A single Windows Locust
process often can't push enough organic load to saturate the Pi on a cheap
endpoint; this overlay fills that gap deterministically.

Scenario detection (pick by env)
--------------------------------
- HTTP mode (default): polls NEXUS_SCENARIO_URL. Use when running on the Pi
  HOST. Default http://localhost/api/scenario.php.
- File mode: set NEXUS_SCENARIO_FILE to the shared JSON the launcher writes
  (/tmp/nexus_active_scenario.json; absent file == idle). Use when running
  INSIDE a container that mounts the shared_tmp volume at /tmp.

Tuning (env vars)
-----------------
  NEXUS_BURN_SCENARIO   scenario name that triggers the load   (default: ddos)
  NEXUS_BURN_WORKERS    busy processes / cores to load          (default: all cores)
  NEXUS_BURN_DUTY       busy fraction per worker, 0.0-1.0       (default: 0.85)
  NEXUS_BURN_RAMP_S     seconds to stagger workers on start     (default: 5)
  NEXUS_POLL_S          scenario poll interval, seconds         (default: 1.0)
  NEXUS_SCENARIO_FILE   active-scenario JSON path (file mode)   (default: unset -> HTTP)
  NEXUS_SCENARIO_URL    scenario endpoint (HTTP mode)           (default: http://localhost/api/scenario.php)

Run
---
  Host:       python3 ddos_cpu_load.py
  Container:  see the docker-compose snippet at the bottom of this file.

Stops cleanly on Ctrl-C / SIGTERM (safe as a systemd service or compose service).

NOTE (thermal): sustained 100% CPU can make a Pi thermal-throttle. The default
duty of 0.85 is intentionally below a hard peg; lower NEXUS_BURN_DUTY if the
board gets hot during long soak runs.
"""

import json
import multiprocessing as mp
import os
import signal
import sys
import time
import urllib.request


# ---- Configuration (env-driven) --------------------------------------------

TARGET_SCENARIO = os.getenv("NEXUS_BURN_SCENARIO", "saturation").strip()
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
    """
    One busy process. Each 100 ms window: spin for `duty * 100 ms`, then sleep
    the rest. That gives ~`duty` of one core; N workers load ~N cores.
    """
    busy = duty * CONTROL_PERIOD
    idle = CONTROL_PERIOD - busy
    # Ignore SIGINT in workers; the parent coordinates shutdown via the event.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    while not stop_evt.is_set():
        t_end = time.perf_counter() + busy
        x = 1.000001
        # Tight arithmetic loop; check the clock every few thousand iterations
        # to keep perf_counter() overhead negligible while staying responsive.
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
        log(f"ddos active -> starting CPU load: {WORKERS} worker(s) @ duty {DUTY:.2f} "
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
    """Return the active scenario name, or 'idle'. Fails safe to 'idle'."""
    if SCEN_FILE:
        try:
            with open(SCEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return (str(data.get("scenario", "idle")) or "idle").strip()
        except FileNotFoundError:
            return "idle"          # launcher deletes the file when idle
        except Exception:
            return "idle"
    # HTTP mode
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


# =============================================================================
# Deploy as a docker-compose service (recommended on the Pi)
# -----------------------------------------------------------------------------
# Reads the shared scenario file directly (no HTTP). Add under `services:` in
# server-pi/docker-compose.yml, then `docker compose up -d cpu-load`:
#
#   cpu-load:
#     image: python:3.11-slim
#     restart: unless-stopped
#     volumes:
#       - ./ddos_cpu_load.py:/app/ddos_cpu_load.py
#       - shared_tmp:/tmp                 # so it can read the active-scenario file
#     working_dir: /app
#     environment:
#       - NEXUS_SCENARIO_FILE=/tmp/nexus_active_scenario.json
#       - NEXUS_BURN_DUTY=0.85            # lower for "a bit" of CPU, raise toward 1.0 to peg
#       # - NEXUS_BURN_WORKERS=3          # default = all cores; cap it to leave headroom
#     command: ["python3", "ddos_cpu_load.py"]
#     depends_on:
#       - web
#
# Or run it straight on the Pi host (HTTP mode, no compose change):
#   python3 ddos_cpu_load.py
# =============================================================================
