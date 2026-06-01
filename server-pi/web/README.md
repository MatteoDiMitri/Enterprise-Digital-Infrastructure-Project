# Web — NEXUS frontend and PHP instrumentation

This folder contains the e-commerce frontend (static HTML/CSS/JS) and
small PHP endpoints used by the observability demo. It is a demo-grade
site intended for university projects and local deployments (Raspberry
Pi, laptops), not for production.

What this component does
------------------------
- Serves the shop UI and static pages (`index.php`, `team.html`, ...).
- Provides a minimal PHP-based telemetry shim that translates Prometheus
  data into a JSON shape consumed by the dashboard UI.
- Exposes Prometheus-compatible metrics (`/api/metrics`) and basic
  system metrics (`/api/system_metrics`).
- Hosts small business endpoints (e.g. `checkout.php`) instrumented to
  emit application and database metrics.

Architecture and key files
--------------------------
- `Dockerfile` — builds the PHP/Apache image used in `docker-compose.yml`.
- `index.php` — shop frontend; embeds DB results into a JS array.
- `dashboard.html` — dashboard UI (Chart.js) that polls `/api/dashboard_metrics`.
- `checkout.php` — instrumented checkout endpoint (writes to DB via PDO).
- `api/` — helper endpoints and instrumentation pieces:
  - `_prepend.php` — auto-prepended instrumentation for every PHP request
  - `_metrics_store.php` — shared metric store (APCu preferred, file fallback)
  - `_pdo_statement.php` — PDO wrapper that measures query latency
  - `metrics.php` — Prometheus text-format exposition endpoint
  - `dashboard_metrics.php` — JSON shim used by `dashboard.html`
  - `scenario.php` — read/write active test scenario (used by the launcher)

Ports and compose notes
-----------------------
- The web service exposes port 80 in the container. In this repository's
  Docker Compose the host port is mapped to `8080:80`.
- The dashboard UI typically polls a small Python backend bound to
  container port `8081` (mapped to host `8881` in compose), but the
  dashboard can fall back to the Apache-hosted JSON shim.

Environment variables
---------------------
- `MYSQL_HOST`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_CHARSET`
  — used by `db.php` to configure the PDO connection. Use `.env` on the
  deployment host; do not commit real credentials to the repository.

Running on a Raspberry Pi
-------------------------
- Ensure the images you use support ARM (use multi-arch images or build
  with `docker buildx` for the Pi). On Raspberry Pi OS / Debian:

```bash
# from repo root
docker compose up -d --build
```

- If you need automatic updates on `git push` to the Pi, use a small
  webhook runner or a GitHub Action that SSHes into the Pi and runs:

```bash
cd /path/to/repo && git pull && docker compose pull && docker compose up -d --build
```

Security notes (important)
--------------------------
- `api/scenario.php` accepts unauthenticated POSTs by default — restrict
  it with a token or source IP allowlist for any real deployment.
- CORS is permissive in `.htaccess` for development convenience. Lock
  down `Access-Control-Allow-Origin` in production.
- Do not commit `.env` with real credentials. Remove tracked secrets and
  rotate database credentials if they were exposed.

Where to look next
------------------
- `web/html/.htaccess` — Apache rewrite rules and auto-prepend config.
- `web/html/api/_metrics_store.php` — how metrics are stored/exposed.

This README focuses on the web layer; see the top-level README for
deployment and Raspberry Pi specific steps for the whole stack.
