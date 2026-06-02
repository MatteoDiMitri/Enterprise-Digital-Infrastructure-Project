"""
launcher/runner.py
==================
Manages exactly ONE running Locust process.

Design choices
--------------
- Single-runner singleton. A laptop typically can't usefully run two
  load tests concurrently anyway, and a single runner makes the
  control-panel UX (one Start / one Stop) match reality.
- Subprocess invoked via `sys.executable -m locust` to bypass PATH
  surprises (works whether `locust` is on PATH or only in a venv).
- stderr merged into stdout (`stderr=subprocess.STDOUT`) so a single
  reader thread captures everything Locust prints. Locust uses both
  streams for normal output; merging avoids interleaving issues.
- Logs go into a bounded ring buffer (`deque(maxlen=N)`) so a long
  run doesn't OOM the launcher process. The control panel only ever
  shows the last N lines.
- Locks: stdlib `threading.Lock` guards every mutation. The reader
  thread writes; the FastAPI request handler reads.

Public surface
--------------
  runner = LocustRunner(project_dir=Path('.'))
  runner.start(scenario, users, spawn_rate, duration, host)
  runner.stop()
  runner.status() -> dict
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---- Configuration ----------------------------------------------------------

MAX_LOG_LINES = 1000          # ring buffer cap; older lines drop off the back
STOP_GRACE_SECONDS = 5        # SIGTERM grace before SIGKILL on stop()


# ---- Helpers ----------------------------------------------------------------

def _now_iso() -> str:
    """ISO-8601 UTC timestamp — what the control panel expects on log lines."""
    return datetime.now(timezone.utc).isoformat()


# Crude classifier: locust prints "[2025-..] WARNING / xxx" and similar.
# We want to color these in the control panel without keeping per-locust-version
# parsers. A line containing 'ERROR' / 'CRITICAL' is error; 'WARNING' is warn;
# anything else is info. The classifier is opinionated and deliberately
# conservative.
LEVEL_PATTERNS = (
    ("error", re.compile(r"\b(ERROR|CRITICAL|Traceback|Exception|failed)\b", re.I)),
    ("warn",  re.compile(r"\bWARN(ING)?\b",                                  re.I)),
    ("ok",    re.compile(r"\b(All users spawned|Test run complete|Shutting down)\b", re.I)),
)


def _classify(line: str) -> str:
    for level, rx in LEVEL_PATTERNS:
        if rx.search(line):
            return level
    return "info"


# ---- LocustRunner -----------------------------------------------------------

class LocustRunner:
    """Owns a single Locust subprocess and exposes start/stop/status."""

    def __init__(self, project_dir: Path, default_host: str = "http://localhost"):
        # project_dir = the directory containing locustfile.py + scenarios/.
        # It's also the cwd we hand to the subprocess so `-f scenarios/x.py`
        # resolves regardless of where the launcher was started from.
        self.project_dir = Path(project_dir).resolve()
        self.default_host = default_host

        self._lock = threading.RLock()        # reentrant — start()/stop() call _append_log() while holding the lock
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._logs: deque = deque(maxlen=MAX_LOG_LINES)

        # Snapshot of the current/most-recent run params, for /status.
        self._scenario: Optional[str]   = None
        self._params:   dict            = {}
        self._started_at: Optional[str] = None
        self._stopped_at: Optional[str] = None

    # ------------------------------------------------------------------ start

    def start(
        self,
        scenario: str,
        users: int,
        spawn_rate: int,
        duration: int,
        host: Optional[str] = None,
    ) -> dict:
        """
        Spawn `locust -f scenarios/{scenario}.py …`.

        Raises RuntimeError if a test is already in progress or the
        scenario file is missing.
        """
        with self._lock:
            if self._proc and self._proc.poll() is None:
                raise RuntimeError("a load test is already running")

            scenario_path = self.project_dir / "scenarios" / f"{scenario}.py"
            if not scenario_path.is_file():
                raise FileNotFoundError(f"scenario file not found: {scenario_path}")

            target_host = host or self.default_host

            # `python -m locust` rather than bare `locust`: works in any venv,
            # avoids PATH ambiguity, and ensures the same interpreter that
            # imported FastAPI is the one running Locust.
            cmd = [
                sys.executable, "-m", "locust",
                "-f", str(scenario_path),
                "--headless",
                "-u", str(users),
                "-r", str(spawn_rate),
                "-t", f"{duration}s",
                "--host", target_host,
                # Force unbuffered output so we get live log streaming.
                # Locust honors `--loglevel INFO` by default, but the python
                # buffering can hide intermediate progress.
            ]

            # Reset run state before we spawn. Older logs stay only if the
            # caller didn't clear them via the UI's Clear button.
            self._scenario   = scenario
            self._params     = {"users": users, "spawn_rate": spawn_rate,
                                "duration": duration, "host": target_host}
            self._started_at = _now_iso()
            self._stopped_at = None

            # Important env tweaks:
            # - PYTHONUNBUFFERED=1: line-by-line log flushes, no buffering
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"

            self._append_log("ok", f"$ {' '.join(shlex.quote(c) for c in cmd)}")

            try:
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=str(self.project_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,   # merge streams
                    bufsize=1,                  # line-buffered
                    text=True,
                    env=env,
                )
            except FileNotFoundError as e:
                self._append_log("error", f"failed to launch locust: {e}")
                raise

            # Background thread to drain the pipe into the ring buffer.
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                args=(self._proc,),
                daemon=True,
                name="locust-log-reader",
            )
            self._reader_thread.start()

            # Notify the Raspberry Pi dashboard which scenario is starting.
            # This is a best-effort, fire-and-forget notification in a
            # background thread: if the Pi is offline or the POST fails the
            # load test should continue. The dashboard may not display the
            # active-scenario banner, but raw metrics (scraped by Prometheus)
            # will keep arriving.
            self._notify_scenario_async(scenario, target_host, self._params)

            return {
                "status": "started",
                "pid": self._proc.pid,
                "scenario": scenario,
                "params": self._params,
            }

    # ------------------------------------------------------------------- stop

    def stop(self) -> dict:
        """SIGTERM the subprocess; SIGKILL after a short grace period."""
        with self._lock:
            proc = self._proc
            if not proc or proc.poll() is not None:
                self._append_log("info", "stop requested but no process is running")
                return {"status": "not_running"}

        # Release the lock while we wait on the OS — wait_for can block.
        self._append_log("warn", f"sending SIGTERM to PID {proc.pid}")
        try:
            proc.terminate()
        except ProcessLookupError:
            pass

        try:
            proc.wait(timeout=STOP_GRACE_SECONDS)
            self._append_log("ok", "process exited cleanly after SIGTERM")
        except subprocess.TimeoutExpired:
            self._append_log("error",
                             f"process did not stop after {STOP_GRACE_SECONDS}s, sending SIGKILL")
            try:
                proc.kill()
                proc.wait(timeout=2)
            except ProcessLookupError:
                pass

        with self._lock:
            self._stopped_at = _now_iso()
            target_host = self._params.get("host")

        # Notify the Raspberry Pi that the scenario has ended. Same
        # best-effort, non-blocking semantics as in `start()`.
        if target_host:
            self._notify_scenario_async("idle", target_host, None)

        return {"status": "stopped"}

    # ----------------------------------------------------------------- status

    def status(self) -> dict:
        """Snapshot for GET /status. Cheap; safe to call every 2s."""
        with self._lock:
            running = bool(self._proc and self._proc.poll() is None)
            # Copy the deque so we don't hand callers a live reference.
            logs = list(self._logs)
            return {
                "running":    running,
                "scenario":   self._scenario,
                "started_at": self._started_at if running else None,
                "stopped_at": self._stopped_at,
                "pid":        self._proc.pid if running and self._proc else None,
                **self._params,           # users / spawn_rate / duration / host
                "logs":       logs,
            }

    # ------------------------------------------------------------ log buffer

    def clear_logs(self) -> None:
        with self._lock:
            self._logs.clear()

    # ------------------------------------------------------------ internals

    def _append_log(self, level: str, message: str) -> None:
        entry = {"ts": _now_iso(), "level": level, "msg": message}
        with self._lock:
            self._logs.append(entry)

    def _notify_scenario_async(
        self,
        scenario: str,
        target_host: str,
        params: Optional[dict],
    ) -> None:
        
        """
            Best-effort notification to the Raspberry Pi dashboard: tells the SUT
            host which scenario is active so the dashboard can display it.

            We POST to TWO endpoints for resilience:
                1) {host}:8881/api/scenario — standalone Python backend
                     (container port 8081; host port 8881 in the repository's compose)
                2) {host}/api/scenario     — PHP/Apache shim (legacy)

            Both calls are best-effort and fire-and-forget. If neither endpoint is
            reachable the dashboard will simply not show the banner — the load
            test proceeds regardless.
        """
        if not target_host or not target_host.startswith(("http://", "https://")):
            return

        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(target_host)
        hostname_only = parsed.hostname or ''
        scheme = parsed.scheme or 'http'
        # Standalone backend on port 8081
        endpoint_standalone = f"{scheme}://{hostname_only}:8881/api/scenario"
        # Apache shim, untouched
        endpoint_apache = target_host.rstrip("/") + "/api/scenario"

        payload = {
            "scenario":   scenario,
            "started_at": _now_iso(),
            "params":     params,
        }

        def _post_to(url: str) -> bool:
            try:
                import urllib.request, json as _json
                data = _json.dumps(payload).encode("utf-8")
                req  = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=2).read()
                return True
            except Exception:
                return False

        def _post_both():
            ok1 = _post_to(endpoint_standalone)
            ok2 = _post_to(endpoint_apache)
            if ok1 or ok2:
                src = "standalone+apache" if (ok1 and ok2) else ("standalone" if ok1 else "apache")
                self._append_log("ok", f"notified Raspberry ({src}): scenario={scenario}")
            else:
                self._append_log("warn",
                    f"scenario notify failed (tried {endpoint_standalone} and {endpoint_apache})")

        threading.Thread(target=_post_both, daemon=True,
                         name=f"scenario-notify-{scenario}").start()

    def _reader_loop(self, proc: subprocess.Popen) -> None:
        """
        Runs in a daemon thread. Reads stdout one line at a time until
        the pipe closes (process exit) and pushes each line into the
        ring buffer with a best-effort level classification.
        """
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                self._append_log(_classify(line), line)
        except Exception as e:
            # If the read pipe dies unexpectedly, surface that, then
            # break out — the process is probably already gone.
            self._append_log("error", f"log reader stopped: {e}")
        finally:
            # Wait briefly for the process to record its exit code, then
            # post a clear marker so the UI knows the run ended.
            try:
                rc = proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                rc = None
            self._append_log(
                "ok" if rc == 0 else "warn",
                f"locust process exited (returncode={rc})",
            )
            with self._lock:
                if proc is self._proc:
                    self._stopped_at = _now_iso()
                target_host = (self._params or {}).get("host")

            # IMPORTANT: Locust may exit on its own when `--run-time`
            # expires. In that case `stop()` is never invoked and any persistent
            # "active scenario" marker the dashboard relies on can remain stale.
            # Notify "idle" here as well so the marker is cleared regardless of
            # how Locust terminated.
            if target_host:
                self._notify_scenario_async("idle", target_host, None)
