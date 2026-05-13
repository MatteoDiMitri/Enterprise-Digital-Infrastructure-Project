"""
NEXUS mock API server.

This is the temporary glue that lets the static HTML dashboard talk to the
Python scenario scripts. It does three things:

  1. GET /api/metrics       -> reads mock_data/metrics_current.json
  2. GET /api/logs?level=…  -> reads mock_data/logs.jsonl
  3. GET /api/experiments   -> reads mock_data/experiments.json
  4. GET /api/state         -> reads mock_data/scenario_state.json
  5. POST /api/scenario/<name>/start -> launches the matching script
  6. POST /api/scenario/stop          -> stops whatever is running (= reset)

When we replace the mock data layer with a real DB / Prometheus backend,
only the body of these endpoints needs to change. The frontend stays
exactly the same.

Run:  python3 mock_api/server.py
Default port: 5055
"""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

ROOT       = Path(__file__).resolve().parent.parent
MOCK_DIR   = ROOT / "mock_data"
SCENARIOS  = ROOT / "scenarios"
FRONTEND   = ROOT / "frontend"

# Map scenario name -> script path. Keeps the API surface clean.
SCENARIO_SCRIPTS = {
    "baseline":        SCENARIOS / "baseline.py",
    "flash_crowd":     SCENARIOS / "flash_crowd.py",
    "error_cascade":   SCENARIOS / "error_cascade.py",
    "db_slowdown":     SCENARIOS / "db_slowdown.py",
    "container_crash": SCENARIOS / "container_crash.py",
    "suspicious":      SCENARIOS / "suspicious.py",
}
RESET_SCRIPT = SCENARIOS / "reset.py"

app = Flask(__name__, static_folder=str(FRONTEND), static_url_path="")
CORS(app)  # so the frontend can poll from file:// or another port


# ── File-read endpoints (will become DB queries later) ──

@app.route("/api/metrics")
def get_metrics():
    path = MOCK_DIR / "metrics_current.json"
    if not path.exists():
        return jsonify({"error": "no metrics yet — start a scenario"}), 404
    return path.read_text(), 200, {"Content-Type": "application/json"}


@app.route("/api/logs")
def get_logs():
    level = request.args.get("level", "all").upper()
    limit = int(request.args.get("limit", 100))
    path  = MOCK_DIR / "logs.jsonl"
    if not path.exists():
        return jsonify([])
    lines = path.read_text().strip().splitlines()
    entries = []
    for line in lines[-limit*4:]:  # over-read so filtering still gives `limit`
        try:
            e = json.loads(line)
            if level != "ALL" and e.get("level") != level:
                continue
            entries.append(e)
        except json.JSONDecodeError:
            continue
    return jsonify(entries[-limit:][::-1])  # newest first


@app.route("/api/experiments")
def get_experiments():
    path = MOCK_DIR / "experiments.json"
    if not path.exists():
        return jsonify({"runs": []})
    return path.read_text(), 200, {"Content-Type": "application/json"}


@app.route("/api/state")
def get_state():
    path = MOCK_DIR / "scenario_state.json"
    if not path.exists():
        return jsonify({"active": None, "started_at": None, "pid": None})
    return path.read_text(), 200, {"Content-Type": "application/json"}


# ── Scenario lifecycle endpoints ──

def _is_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


@app.route("/api/scenario/<name>/start", methods=["POST"])
def start_scenario(name):
    if name not in SCENARIO_SCRIPTS:
        return jsonify({"error": f"unknown scenario '{name}'"}), 400

    # Check if another scenario is already running.
    state_path = MOCK_DIR / "scenario_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            if state.get("active") and _is_alive(state.get("pid")):
                return jsonify({
                    "error": "another scenario is running",
                    "active": state["active"],
                }), 409
        except json.JSONDecodeError:
            pass

    # Optional duration override from the request body.
    duration = None
    if request.is_json:
        duration = request.json.get("duration")

    cmd = [sys.executable, str(SCENARIO_SCRIPTS[name])]
    if duration:
        cmd.append(str(int(duration)))

    # Launch detached so the API process is not blocked.
    proc = subprocess.Popen(
        cmd,
        cwd=str(SCENARIOS),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return jsonify({"started": name, "pid": proc.pid, "duration": duration})


@app.route("/api/scenario/stop", methods=["POST"])
def stop_scenario():
    """Stop = run the reset script."""
    proc = subprocess.Popen(
        [sys.executable, str(RESET_SCRIPT)],
        cwd=str(SCENARIOS),
    )
    proc.wait(timeout=5)
    return jsonify({"stopped": True})


# ── Static frontend ──

@app.route("/")
def index():
    return send_from_directory(str(FRONTEND), "dashboard.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(str(FRONTEND), filename)


if __name__ == "__main__":
    MOCK_DIR.mkdir(exist_ok=True)
    print("NEXUS mock API")
    print(f"  scenarios at: {SCENARIOS}")
    print(f"  mock data at: {MOCK_DIR}")
    print(f"  frontend at : {FRONTEND}")
    print("  open http://localhost:5055/  in your browser")
    app.run(host="0.0.0.0", port=5055, debug=False)
