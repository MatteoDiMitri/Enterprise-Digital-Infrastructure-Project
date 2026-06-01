# NEXUS — Locust Load Testing Suite

Local Locust load-testing rig for the PHP shop, driven from the
`control_panel.html` UI.

```
┌──────────────────┐    HTTP    ┌──────────────────┐   subprocess   ┌──────────────────┐
│ control_panel    │ ─────────▶ │ FastAPI launcher │ ─────────────▶ │ locust headless  │ ──▶ shop
│ .html (browser)  │            │  port 8000       │                │  (one subprocess)│
└──────────────────┘            └──────────────────┘                └──────────────────┘
```

## Project structure

```
load-generator/
├── README.md
├── requirements.txt
├── locustfile.py                  # default entry; runs the Normal scenario
├── scenarios/
│   ├── _base.py                   # shared ShopUser + product catalog
│   ├── normal.py                  # baseline traffic
│   ├── flash_crowd.py             # viral spike (LoadTestShape ramp)
│   ├── ddos.py                    # GET-only flood (educational)
│   ├── checkout_storm.py          # POST /checkout.php pressure
│   ├── degradation.py             # tail-latency + bad-URL mix
│   └── saturation.py              # max throughput, ~0 think time
└── launcher/
    ├── main.py                    # FastAPI app (3 endpoints)
    └── runner.py                  # subprocess + log streaming
```

## Setup

```bash
cd load-generator/
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

### 1. Start the FastAPI launcher

```bash
# from the locust/ directory
uvicorn launcher.main:app --host 127.0.0.1 --port 8000 --reload
```

Confirm it's up:

```bash
curl http://127.0.0.1:8000/
```

### 2. Open the control panel

Open `control_panel.html` in your browser (double-click, or serve it
alongside the PHP shop). The page polls `http://localhost:8000/status`
every 2 seconds; if the launcher is reachable the top-bar shows
`launcher: online` in green.

In the "Launcher endpoint" field at the bottom, set the launcher URL
(default `http://localhost:8000` is fine when both run on the same
laptop). In "Target host" set the PHP shop's URL — e.g.
`http://localhost` for XAMPP/MAMP locally, or
`http://your-server.example` for a remote test target.

### 3. Pick a scenario, set parameters, hit Start

- Clicking a scenario card pre-fills sensible parameter defaults.
- START / STOP buttons map to `POST /start-test` and `POST /stop-test`.
- Execution log streams from the same subprocess output Locust would
  print to your terminal.

## Direct CLI usage (without the control panel)

Each scenario file is a normal Locust file; you can invoke any of them
directly:

```bash
locust -f scenarios/normal.py         --headless -u 100  -r 10  -t 120s --host http://localhost
locust -f scenarios/flash_crowd.py    --headless -u 2000 -r 200 -t 120s --host http://localhost
locust -f scenarios/ddos.py           --headless -u 1500 -r 300 -t  90s --host http://localhost
locust -f scenarios/checkout_storm.py --headless -u 500  -r 50  -t 120s --host http://localhost
locust -f scenarios/degradation.py    --headless -u 300  -r 20  -t 180s --host http://localhost
locust -f scenarios/saturation.py     --headless -u 5000 -r 500 -t 120s --host http://localhost
```

Or run with the Locust web UI:

```bash
locust -f scenarios/normal.py --host http://localhost
# then open http://localhost:8089
```

## API reference (launcher)

### `POST /start-test`

```json
{
  "scenario":   "flash_crowd",
  "users":      2000,
  "spawn_rate": 200,
  "duration":   120,
  "host":       "http://localhost"
}
```

- `409` if a test is already running.
- `400` if `scenario` is not in the allow-list.

### `POST /stop-test`

No body. SIGTERMs the running process; SIGKILL after 5s grace.

### `GET /status`

```json
{
  "running": true,
  "scenario": "flash_crowd",
  "started_at": "2026-05-22T13:42:01.123Z",
  "stopped_at": null,
  "pid": 12345,
  "users": 2000, "spawn_rate": 200, "duration": 120,
  "host": "http://localhost",
  "logs": [
    {"ts": "...", "level": "info",  "msg": "Starting Locust 2.x"},
    {"ts": "...", "level": "ok",    "msg": "All users spawned"},
    {"ts": "...", "level": "error", "msg": "POST /checkout.php: 500"}
  ]
}
```

## Configuration

| Env var              | Default              | Purpose                                  |
| -------------------- | -------------------- | ---------------------------------------- |
| `LOCUST_TARGET_HOST` | `http://localhost`   | Default target if `host` omitted in body |

## Notes on `flash_crowd` and `LoadTestShape`

`scenarios/flash_crowd.py` defines a `FlashCrowdShape(LoadTestShape)`
that drives a three-stage ramp (warm-up → burst → hold). When a shape
is present, Locust **ignores** the `-u`, `-r`, and `-t` flags — the
shape itself owns the user count, spawn rate, and duration.

You'll see a warning like this in the execution log when running
`flash_crowd`:

```
The following option(s) will be ignored: --run-time
--run-time, --users or --spawn-rate have no impact on LoadShapes …
```

That's expected. The shape ramps 100 → 2000 users over ~25s and holds
for ~65s. Adjust those numbers by editing `FlashCrowdShape.stages` in
`scenarios/flash_crowd.py`.

## Extending: add a new scenario

1. Create `scenarios/<name>.py`.
2. Subclass `ShopUser` (from `_base.py`) or `HttpUser` directly; set
   `wait_time` / `tasks` / `LoadTestShape` as needed.
3. Add `"<name>"` to `ALLOWED_SCENARIOS` in `launcher/main.py`.
4. Add the scenario card to the `SCENARIOS` array in
   `control_panel.html`.

That's it — no Locust restart needed (the next `/start-test` picks up
the new file).
