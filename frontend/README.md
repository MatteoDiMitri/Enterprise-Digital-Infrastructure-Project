# NEXUS — Observability Dashboard

University project: infrastructure observability, performance, dependability,
QoS, benchmarking and incident scenarios. Designed to run on a Raspberry Pi.

```
nexus/
├── frontend/                      Static HTML/CSS/JS
│   ├── dashboard.html            ← the observability dashboard (polls the API)
│   ├── shop.html
│   └── team.html
├── scenarios/                     Python scripts — one per scenario
│   ├── metrics_writer.py         ← shared helper: writes to the mock files
│   ├── baseline.py
│   ├── flash_crowd.py
│   ├── error_cascade.py
│   ├── db_slowdown.py
│   ├── container_crash.py
│   ├── suspicious.py
│   └── reset.py
├── mock_api/
│   └── server.py                 ← Flask bridge: serves files, launches scripts
├── mock_data/                     Auto-created. The "database" for now.
│   ├── metrics_current.json      ← latest snapshot (Prometheus-style)
│   ├── logs.jsonl                ← append-only structured log
│   ├── scenario_state.json       ← which scenario is currently running
│   └── experiments.json          ← summary table history
└── requirements.txt
```

## Quick start

```bash
pip install -r requirements.txt
python3 mock_api/server.py
# open http://localhost:5055/
```

That's it. The Flask server serves the frontend and the API from the same port,
so there are no CORS issues to fight.

## Data flow

```
┌─────────────────┐   1) POST /api/scenario/<name>/start
│  dashboard.html │ ──────────────────────────────────────┐
└─────────────────┘                                       │
        ▲                                                 ▼
        │ 4) GET /api/metrics                  ┌────────────────────┐
        │    GET /api/logs                     │   mock_api/server  │
        │    GET /api/experiments              │   (Flask)          │
        │    GET /api/state                    └────────────────────┘
        │                                        │            ▲
        │                                  2) spawn           │
        │                                        ▼            │
        │                              ┌──────────────────┐   │
        │                              │ scenarios/<x>.py │   │
        │                              │  (~1.5s ticks)   │   │
        │                              └──────────────────┘   │
        │                                        │            │
        │                                  3) write           │ 4) read
        │                                        ▼            │
        │                              ┌────────────────────┐ │
        └──────────────────────────────│   mock_data/*.json │─┘
                                       └────────────────────┘
```

1. User clicks a scenario button. JS does `POST /api/scenario/flash_crowd/start`.
2. The Flask server launches the corresponding Python script as a detached
   subprocess and records its PID in `scenario_state.json`.
3. The script ticks every ~1.5s, writing the current metrics snapshot to
   `metrics_current.json`, appending log lines to `logs.jsonl`, and (when it
   finishes) appending a row to `experiments.json`.
4. The frontend polls `/api/metrics`, `/api/logs`, `/api/experiments` and
   `/api/state` every 1.5s and re-renders. Charts use the 60-point history
   the backend keeps for them.

## Reset

The "Reset System" button sends `POST /api/scenario/stop`, which runs
`reset.py`. That script:

  - kills the active scenario process (using the PID stored in the state file);
  - clears the state file;
  - rewrites `metrics_current.json` with a clean healthy baseline snapshot;
  - appends an INFO log entry.

It deliberately leaves `experiments.json` alone, so the summary table keeps
its history across resets.

## What the scenarios actually simulate

| Scenario          | Duration | Signature                                          |
|-------------------|----------|----------------------------------------------------|
| Baseline          | 120s     | ~40 RPS, ~180ms P99, all green                     |
| Flash crowd       | 90s      | RPS 10×, queue saturates, P99 → ~7s, SLO breach   |
| Error cascade     | 90s      | err_rate ~40%, fastapi degrades, then nginx       |
| DB slowdown       | 120s     | DB latency 12 → 800ms, /health stays fast         |
| Container crash   | 90s      | fastapi DOWN, then warm-up, MTTR ~25s             |
| Suspicious        | 90s      | 4xx spike from rate-limit, 5xx stays low, SLO OK  |
| Reset             | instant  | clears state, restores baseline snapshot          |

Each script produces metrics with phase-appropriate shapes (linear ramps,
non-linear queueing latency, etc.) and logs that match the phase. Numbers
are not pure noise.

## Mock files in detail

### `mock_data/metrics_current.json`

```jsonc
{
  "timestamp": "2026-05-11T13:45:21+00:00",
  "scenario": "flash_crowd",
  "scenario_started_at": "2026-05-11T13:44:12+00:00",
  "kpi": {
    "rps": 412, "latency_p50": 78, "latency_p95": 1432, "latency_p99": 3805,
    "error_rate": 3.8, "availability": 96.7,
    "cpu": 87, "memory": 71, "queue_depth": 156,
    "active_services": "5/6", "slo_status": "violation"
  },
  "history": {
    "rps":         [42, 58, 95, ... 60 points],
    "latency_p99": [180, 220, 410, ...],
    /* ...etc, 60-point rolling window for the charts */
  },
  "status_distribution": { "2xx": 380, "3xx": 12, "4xx": 8, "5xx": 12 },
  "services": {
    "fastapi":  { "status": "degraded", "latency": 145, "error_rate": 4.2, "uptime": 96.7 },
    "nginx":    { "status": "warning",  "latency": 38,  "error_rate": 1.1, "uptime": 99.5 },
    /* ...etc */
  },
  "endpoints": [ /* per-endpoint breakdown for the table */ ]
}
```

### `mock_data/logs.jsonl`

One JSON object per line — easy to `tail -f` from the shell, easy to grep,
easy to stream later from a real log aggregator.

```json
{"timestamp": "2026-05-11T13:45:21+00:00", "level": "WARN", "service": "nginx", "message": "upstream queue depth 156 — approaching limit"}
{"timestamp": "2026-05-11T13:45:22+00:00", "level": "ERROR", "service": "fastapi", "message": "worker pool saturated — request queued"}
```

### `mock_data/experiments.json`

```jsonc
{
  "runs": [
    {
      "id": 1747842512,
      "scenario": "flash_crowd",
      "started_at": "2026-05-11T13:44:12+00:00",
      "duration_sec": 90,
      "peak_rps": 468,
      "peak_latency_p99": 7305,
      "peak_error_rate": 5.22,
      "recovery_sec": 28,
      "slo_met": false,
      "notes": "Flash crowd 10× baseline — queue saturation caused P99 latency breach."
    }
  ]
}
```

### `mock_data/scenario_state.json`

```json
{ "active": "flash_crowd", "started_at": "2026-05-11T13:44:12+00:00", "pid": 18432 }
```

## Migration path to a real backend

The mock layer was designed with this migration in mind. The HTTP contract
between frontend and server stays exactly the same — only the four read
endpoints in `mock_api/server.py` change.

| Mock file             | Becomes…                                                   |
|-----------------------|------------------------------------------------------------|
| `metrics_current.json`| A query against Prometheus: `GET /api/v1/query?query=…`    |
| `logs.jsonl`          | A query against Loki / journald / a logs DB               |
| `experiments.json`    | A table `experiments` in PostgreSQL                       |
| `scenario_state.json` | A row in PostgreSQL or a Redis key                        |

So `get_metrics()` in `server.py` goes from this:

```python
def get_metrics():
    return (MOCK_DIR / "metrics_current.json").read_text()
```

to something like this:

```python
def get_metrics():
    rps = prom.query("rate(http_requests_total[1m])")
    p99 = prom.query("histogram_quantile(0.99, ...)")
    # …assemble the same JSON shape and return it
    return jsonify(payload)
```

The frontend doesn't change at all — it still polls the same four URLs and
expects the same JSON shape.

Similarly, the scenario scripts can stop writing to JSON files and instead:
  - drive a real Locust / hey / k6 load generator
  - run `docker kill fastapi` for the container crash scenario
  - run a deliberately slow SQL query for the DB scenario
  - replace `iptables` / nginx rate-limit config for the suspicious scenario

The Flask launcher already knows how to run arbitrary Python scripts, so the
only thing that changes is the body of each scenario file.

## Notes for the Raspberry Pi

  - Polling is 1.5s, atomic file writes prevent half-read JSON.
  - `logs.jsonl` is auto-truncated to 2000 lines so it never balloons.
  - Each scenario is a single short-lived Python process — no daemons.
  - No external services required (no Redis, no DB) for the mock layer to work.
  - The whole thing including Flask runs comfortably under 80 MB resident.
