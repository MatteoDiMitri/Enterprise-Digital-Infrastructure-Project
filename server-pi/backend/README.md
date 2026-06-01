# Backend — NEXUS dashboard server

This folder contains a small, dependency-free Python HTTP server used
by the NEXUS observability demo. The server queries Prometheus and
exposes a compact JSON API consumed by the dashboard UI.

What it does
------------
- Queries Prometheus with a small set of PromQL expressions to build
  KPI values, history series, endpoint summaries and service health.
- Serves four endpoints:
  - `GET /api/dashboard_metrics` — aggregated telemetry for the UI
  - `GET /api/scenario` — returns the currently active scenario (file)
  - `POST /api/scenario` — set the active scenario (used by the launcher)
  - `GET /healthz` — basic health check
- Designed to run independently from the Apache/PHP stack so dashboard
  rendering remains responsive under heavy load.

Implementation and architecture
-------------------------------
- Single-file implementation: `dashboard-server.py` (stdlib only — no
  external dependencies).
- Uses `http.server` with a small thread-per-request server mixin for
  concurrent handling of short requests.
- Prometheus queries are performed via `urllib.request` with a short
  timeout; helper functions wrap instant and range queries.
- Active scenario is stored as a JSON file at `/tmp/nexus_active_scenario.json`.

Ports and Docker mapping
------------------------
- The server process listens on **container port 8081** by default
  (`NEXUS_BIND_PORT`).
- In the repository's `docker-compose.yml` the container port 8081 is
  mapped to **host port 8881** (`8881:8081`).
- When running the script directly on a host, access it at port 8081.

Environment variables
---------------------
- `NEXUS_PROM_URL` — Prometheus base URL (default: `http://prometheus:9090`).
- `NEXUS_BIND_HOST` — host to bind (default: `0.0.0.0`).
- `NEXUS_BIND_PORT` — port to bind (default: `8081`).

Usage
-----
Run locally:

```bash
python3 backend/dashboard-server.py
```

Under Docker Compose (from project root):

```bash
docker compose up -d --build
# Access dashboard API at http://<host>:8881 (compose mapping)
```

Security notes
--------------
- The `/api/scenario` POST endpoint is unauthenticated by default; in
  production you should protect it (token header, firewall, or
  allowlist). See `web/html/api/scenario.php` for the PHP shim equivalent.
- CORS is permissive in the demo; restrict allowed origins in real
  deployments.
- Do not expose Prometheus UI or this backend to the public internet
  without authentication and network controls.

Troubleshooting
---------------
- Logs are printed to stdout/stderr — when running in Docker use
  `docker compose logs -f dashboard-backend`.
- Test Prometheus connectivity from inside the container:

```bash
# open a shell in the running container
docker compose exec dashboard-backend bash
python3 -c "import urllib.request; print(urllib.request.urlopen('http://prometheus:9090/').status)"
```

Notes & recommended improvements
-------------------------------
- Add a simple token check for `/api/scenario` to prevent unauthorized
  modifications.
- Replace permissive CORS with an environment-configured allowlist.
- Consider moving the scenario storage from a flat file to a small
  IPC mechanism if concurrent writes become an issue.

Files
-----
- `dashboard-server.py` — main server implementation (this folder).

---