"""
launcher/main.py
================
FastAPI app that the local HTML control panel talks to. Three endpoints,
matching the contract baked into control_panel.html:

  POST /start-test    body: {scenario, users, spawn_rate, duration, host?}
  POST /stop-test     no body
  GET  /status        snapshot {running, scenario, started_at, ..., logs}

Run with:

    uvicorn launcher.main:app --host 127.0.0.1 --port 8000 --reload

The launcher is intentionally tiny: routing + validation + delegate to
the singleton LocustRunner. All state (subprocess handle, log buffer,
timestamps) lives in the runner, so adding more endpoints later
(metrics, history, scenario listing) stays clean.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .runner import LocustRunner


# ---- Configuration ----------------------------------------------------------

# The locust project root: parent of this file's parent (launcher/main.py
# is two levels deep relative to the project root).
PROJECT_DIR = Path(__file__).resolve().parent.parent

# Default target host if the request doesn't specify one. Overridable
# via the LOCUST_TARGET_HOST env var.
import os
DEFAULT_HOST = os.environ.get("LOCUST_TARGET_HOST", "http://localhost")

# Whitelist: only these scenario keys map to a *.py file in scenarios/.
# Kept explicit (rather than a glob) so a stray file in scenarios/ can't
# be invoked over the public-ish API.
ALLOWED_SCENARIOS = {
    "normal",
    "flash_crowd",
    "ddos",
    "checkout_storm",
    "degradation",
    "saturation",
}


# ---- Pydantic schemas -------------------------------------------------------

class StartRequest(BaseModel):
    scenario:   str = Field(..., description="one of the keys in ALLOWED_SCENARIOS")
    users:      int = Field(..., ge=1,   le=20_000)
    spawn_rate: int = Field(..., ge=1,   le=2_000)
    duration:   int = Field(..., ge=5,   le=3600, description="seconds")
    host:       Optional[str] = Field(None, description="target URL; defaults to LOCUST_TARGET_HOST")


# ---- App --------------------------------------------------------------------

app = FastAPI(
    title="NEXUS Locust Launcher",
    version="1.0.0",
    description="Local subprocess orchestrator for Locust load-testing scenarios.",
)

# CORS: the control panel may be opened from file:// or served from a
# different origin (the PHP shop's host). Allow everything in dev — this
# launcher is meant to bind to 127.0.0.1 only, so there's no real exposure.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

runner = LocustRunner(project_dir=PROJECT_DIR, default_host=DEFAULT_HOST)


# ---- Routes -----------------------------------------------------------------

@app.post("/start-test")
def start_test(body: StartRequest):
    """
    Launch a Locust subprocess for the requested scenario.

    Returns 409 if a test is already running, 400 if the scenario key
    is unknown, 500 if the subprocess fails to spawn.
    """
    if body.scenario not in ALLOWED_SCENARIOS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown scenario '{body.scenario}'. "
                   f"Allowed: {sorted(ALLOWED_SCENARIOS)}",
        )
    try:
        return runner.start(
            scenario=body.scenario,
            users=body.users,
            spawn_rate=body.spawn_rate,
            duration=body.duration,
            host=body.host,
        )
    except RuntimeError as e:
        # Already running.
        raise HTTPException(status_code=409, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/stop-test")
def stop_test():
    """Send SIGTERM to the running Locust process (no-op if idle)."""
    return runner.stop()


@app.get("/status")
def status():
    """Snapshot for the control panel's 2-second poll."""
    return runner.status()


# ---- Convenience: small "is the launcher up?" endpoint ----------------------

@app.get("/")
def root():
    return {
        "name": "NEXUS Locust Launcher",
        "scenarios": sorted(ALLOWED_SCENARIOS),
        "default_host": DEFAULT_HOST,
        "project_dir": str(PROJECT_DIR),
    }
