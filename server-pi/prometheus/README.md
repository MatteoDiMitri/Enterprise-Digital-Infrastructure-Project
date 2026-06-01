# Prometheus — NEXUS demo

This folder contains the Prometheus configuration used by the NEXUS Docker Compose demo.
Prometheus scrapes the demo services running inside the Compose network and makes metrics available for inspection and alerting.

## What this does

- Provides a small, local Prometheus instance configured to scrape:
  - Application metrics exposed by the PHP app at `/api/metrics` (`job_name: nexus_php`).
  - Kernel/system metrics exposed by the web container at `/api/system_metrics` (`job_name: nexus_system`).
  - Prometheus itself (`job_name: prometheus`) for self-monitoring.

- Adds an `external_labels.monitor` value so scraped samples can be identified when multiple instances are running.

## Architecture

- Prometheus runs as a container defined in the repository's `docker-compose.yml` and scrapes targets by container name (e.g. `web:80`, `prometheus:9090`).
- The PHP service in `web/` instruments application behavior and exposes metrics via `web/html/api/metrics.php` (custom in-memory/APCu or file-backed store).
- The same web container exposes a simple system metrics endpoint (`/api/system_metrics`) that reads `/proc` and returns a small set of gauges (CPU, memory, load, uptime).
- The dashboard backend (Python) queries Prometheus directly to build dashboard payloads; the UI polls the dashboard or the PHP shim depending on configuration.

## How to run

From the repository root:

```bash
# Build and start the full stack
docker compose up -d --build
```

Prometheus UI is exposed according to `docker-compose.yml` (default mapping in this repo: host port `9091` → container `9090`). Open `http://<host>:9091` to inspect targets, metrics and query data.

## Configure / customize

- Edit `prometheus.yml` to add or modify scrape jobs. After editing, restart the Prometheus container:

```bash
docker compose restart prometheus
```

- To add a new job, follow the existing `scrape_configs` entries: provide `job_name`, `metrics_path`, and `static_configs.targets` (use container hostnames inside Compose).

Example new job:

```yaml
- job_name: 'my_service'
  metrics_path: /metrics
  static_configs:
    - targets: ['myservice:8080']
      labels:
        service: 'myservice'
```

## Security & deployment notes

- Do not expose Prometheus UI or metrics endpoints to the public internet. Restrict access via firewall, bind to localhost, or place behind an authenticated reverse proxy.
- On single-board computers (Raspberry Pi) confirm the Prometheus image supports your architecture (`arm64` vs `armv7`). Adjust scrape frequency (`scrape_interval`) to reduce load on constrained devices.
- Keep `prometheus.yml` and any credentials out of public repos.

## Troubleshooting

- Check Prometheus logs:

```bash
docker compose logs -f prometheus
```

- Verify target endpoints directly (example):

```bash
curl http://localhost:8080/api/metrics
curl http://localhost:8080/api/system_metrics
```

- If a target is `DOWN` in the Prometheus UI, confirm the container name and port are reachable from the Prometheus container.

## Further reading

- Prometheus docs: https://prometheus.io/docs/

---
