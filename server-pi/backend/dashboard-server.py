#!/usr/bin/env python3
"""
dashboard-server.py — Standalone HTTP backend for the NEXUS dashboard.

Purpose
-------
Lightweight HTTP server (stdlib only) that queries Prometheus and
exposes a small JSON API consumed by the dashboard UI. Run this
separately from Apache/PHP so the dashboard remains responsive under
high load.

Main endpoints
--------------
- GET  /api/dashboard_metrics   : aggregated Prometheus queries → JSON
- GET  /api/scenario            : current active scenario (file-backed)
- POST /api/scenario            : set active scenario (used by launcher)
- GET  /healthz                 : simple health check

Ports and Docker notes
----------------------
- The process binds to port 8081 by default (`NEXUS_BIND_PORT`).
- In this repository's Docker Compose the container port 8081 is
  mapped to host port 8881 (see `docker-compose.yml`).
- If you run the script directly on a host, use port 8081.

Environment variables (defaults)
--------------------------------
- `NEXUS_PROM_URL`   (default: http://prometheus:9090)
- `NEXUS_BIND_HOST`  (default: 0.0.0.0)
- `NEXUS_BIND_PORT`  (default: 8081)

Usage
-----
    python3 backend/dashboard-server.py
    # or run via systemd for production-like deployments
"""

import http.server
import socketserver
import urllib.request
import urllib.parse
import urllib.error
import json
import os
import time
import datetime      # <-- ADD THIS
import docker        # <-- ADD THIS

# Configuration (environment)
PROM_URL = os.environ.get('NEXUS_PROM_URL', 'http://prometheus:9090')
BIND_HOST = os.environ.get('NEXUS_BIND_HOST', '0.0.0.0')
BIND_PORT = int(os.environ.get('NEXUS_BIND_PORT', '8081'))
SCENARIO_FILE = '/tmp/nexus_active_scenario.json'

# --- ADD THIS NEW DOCKER BLOCK ---
try:
    docker_client = docker.from_env()
except Exception as e:
    print(f"Failed to connect to Docker: {e}")
    docker_client = None

# Query window and sampling
RANGE_WINDOW_SEC = 90
RANGE_STEP_SEC = 2          # Prometheus requires integer step values (seconds)
RATE_WINDOW = '30s'         # 30s window: with a 2s scrape that's ~15 samples
                            # per point — responsive percentiles/rps and no
                            # 1-minute "DRAIN" tail after a scenario ends.
HISTORY_POINTS = RANGE_WINDOW_SEC // RANGE_STEP_SEC   # default: 45

ALLOWED_SCENARIOS = {
    'idle', 'normal', 'flash_crowd', 'ddos',
    'checkout_storm', 'degradation', 'saturation',
}

# HTTP timeout for Prometheus calls — prefer a timely response over blocking
PROM_TIMEOUT = 2.0


# ─── Prometheus client ─────────────────────────────────────────────────

def prom_get(path: str) -> dict | None:
    """Request Prometheus at `PROM_URL + path`. Return parsed JSON or None."""
    url = PROM_URL + path
    try:
        with urllib.request.urlopen(url, timeout=PROM_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def prom_instant(promql: str) -> float:
    """Run a Prometheus instant query and return a scalar float (0.0 on error)."""
    q = urllib.parse.quote(promql)
    data = prom_get(f'/api/v1/query?query={q}')
    if not data or data.get('status') != 'success':
        return 0.0
    results = data.get('data', {}).get('result', [])
    if not results:
        return 0.0
    return _safe_float(results[0].get('value', [None, None])[1])


def _safe_float(v) -> float:
    """Safely convert Prometheus string values to float; treat invalids as 0.0."""
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (ValueError, TypeError):
        return 0.0
    # NaN != NaN is the canonical NaN check; isinf catches ±Inf.
    if f != f or f == float('inf') or f == float('-inf'):
        return 0.0
    return f


def prom_range(promql: str, points: int = HISTORY_POINTS) -> list[float]:
    """Run a Prometheus range query and return a fixed-length list of floats."""
    now = int(time.time())
    start = now - RANGE_WINDOW_SEC
    q = urllib.parse.quote(promql)
    url = (f'/api/v1/query_range'
           f'?query={q}&start={start}&end={now}&step={RANGE_STEP_SEC}s')
    data = prom_get(url)
    if not data or data.get('status') != 'success':
        return [0.0] * points
    results = data.get('data', {}).get('result', [])
    if not results:
        return [0.0] * points
    values = results[0].get('values', [])
    series = [_safe_float(v[1] if len(v) > 1 else 0) for v in values]
    if len(series) >= points:
        return series[-points:]
    return [0.0] * (points - len(series)) + series


def prom_query_all(promql: str) -> list[dict]:
    """Run an instant query and return full result series (list of dicts)."""
    q = urllib.parse.quote(promql)
    data = prom_get(f'/api/v1/query?query={q}')
    if not data or data.get('status') != 'success':
        return []
    return data.get('data', {}).get('result', [])


# ─── Metrics aggregation ───────────────────────────────────────────────

def collect_dashboard_metrics() -> dict:
    """Build the JSON payload the dashboard.html expects."""

    # KPIs
    rps   = prom_instant(f'sum(rate(nexus_http_requests_total[{RATE_WINDOW}]))')
    p99   = prom_instant(
        f'histogram_quantile(0.99, sum by(le) '
        f'(rate(nexus_http_request_duration_seconds_bucket[{RATE_WINDOW}]))) * 1000'
    )
    err_rate = prom_instant(
        f'100 * sum(rate(nexus_http_errors_total[{RATE_WINDOW}]))'
        f' / clamp_min(sum(rate(nexus_http_requests_total[{RATE_WINDOW}])), 1)'
    )
    cpu = prom_instant('nexus_system_cpu_percent')
    queue_depth = prom_instant('nexus_active_requests_inflight')

    # User-perceived availability (success AND fast)
    total_rate = prom_instant(f'sum(rate(nexus_http_requests_total[{RATE_WINDOW}]))')
    errors_rate = prom_instant(f'sum(rate(nexus_http_errors_total[{RATE_WINDOW}]))')
    within_slo  = prom_instant(
        f'sum(rate(nexus_http_request_duration_seconds_bucket{{le="1"}}[{RATE_WINDOW}]))'
    )
    if total_rate > 0:
        success_fast = max(0, within_slo - errors_rate)
        availability = (success_fast / total_rate) * 100
    else:
        availability = 100.0
    availability = max(0.0, min(100.0, availability))

    # History (charts)
    history = {
        'latency_p50': prom_range(
            f'histogram_quantile(0.50, sum by(le) '
            f'(rate(nexus_http_request_duration_seconds_bucket[{RATE_WINDOW}]))) * 1000'),
        'latency_p95': prom_range(
            f'histogram_quantile(0.95, sum by(le) '
            f'(rate(nexus_http_request_duration_seconds_bucket[{RATE_WINDOW}]))) * 1000'),
        'latency_p99': prom_range(
            f'histogram_quantile(0.99, sum by(le) '
            f'(rate(nexus_http_request_duration_seconds_bucket[{RATE_WINDOW}]))) * 1000'),
        'rps':         prom_range(f'sum(rate(nexus_http_requests_total[{RATE_WINDOW}]))'),
        'error_rate':  prom_range(
            f'100 * sum(rate(nexus_http_errors_total[{RATE_WINDOW}]))'
            f' / clamp_min(sum(rate(nexus_http_requests_total[{RATE_WINDOW}])), 1)'),
        'queue_depth': prom_range('nexus_active_requests_inflight'),
        'db_latency':  prom_range(
            f'histogram_quantile(0.95, sum by(le) '
            f'(rate(nexus_db_query_duration_seconds_bucket[{RATE_WINDOW}]))) * 1000'),
        'cpu':         prom_range('nexus_system_cpu_percent'),
        'memory':      prom_range('nexus_system_memory_used_percent'),
    }

    # Status distribution (donut)
    status_distribution = {
        '2xx': int(prom_instant(f'sum(rate(nexus_http_requests_total{{status=~"2.."}}[{RATE_WINDOW}]))') * 60),
        '3xx': int(prom_instant(f'sum(rate(nexus_http_requests_total{{status=~"3.."}}[{RATE_WINDOW}]))') * 60),
        '4xx': int(prom_instant(f'sum(rate(nexus_http_errors_total{{type="4xx"}}[{RATE_WINDOW}]))') * 60),
        '5xx': int(prom_instant(f'sum(rate(nexus_http_errors_total{{type="5xx"}}[{RATE_WINDOW}]))') * 60),
    }

    # Services
    services = collect_services(p99, err_rate, cpu)
    healthy_count = sum(1 for s in services.values() if s['status'] == 'healthy')
    active_services_str = f'{healthy_count}/{len(services)}'

    # Endpoints
    endpoints = collect_endpoints()

    # Scenario
    scenario_data = read_scenario_file()
    scenario_name = scenario_data.get('scenario', 'idle')

    # SLO compliance heuristic. CPU threshold aligned to the dashboard's
    # traffic light (red ≥70): >70% → at_risk, >90% → violation.
    if p99 > 1000 or err_rate > 10 or cpu > 90:
        slo_status = 'violation'
    elif p99 > 500 or err_rate > 2 or cpu > 70:
        slo_status = 'at_risk'
    else:
        slo_status = 'ok'

    return {
        'kpi': {
            'rps':             round(rps, 1),
            'latency_p99':     int(round(p99)),
            'error_rate':      round(err_rate, 2),
            'availability':    round(availability, 2),
            'active_services': active_services_str,
            'cpu':             int(round(cpu)),
            'queue_depth':     int(round(queue_depth)),
            'slo_status':      slo_status,
        },
        'history':              history,
        'status_distribution':  status_distribution,
        'services':             services,
        'endpoints':            endpoints,
        'scenario':             scenario_name,
    }


def collect_services(p99: float, err_rate: float, cpu: float) -> dict:
    """Status of the 5 tracked services. Same heuristics as the PHP shim."""
    db_queries = prom_instant(f'sum(rate(nexus_db_queries_total[{RATE_WINDOW}]))')
    db_p95     = prom_instant(
        f'histogram_quantile(0.95, sum by(le) '
        f'(rate(nexus_db_query_duration_seconds_bucket[{RATE_WINDOW}]))) * 1000'
    )

    # PHP
    if cpu > 90:
        php_status = 'degraded'
    elif p99 > 1000 or err_rate > 5:
        php_status = 'warning'
    else:
        php_status = 'healthy'

    # MariaDB: warning only if queries are observed AND slow
    if db_queries > 0 and db_p95 > 50:
        maria_status = 'warning'
    else:
        maria_status = 'healthy'

    # Locust
    scenario_data = read_scenario_file()
    sc = scenario_data.get('scenario', 'idle')
    locust_status = 'healthy' if sc not in ('idle', '') else 'down'

    return {
        'apache':     {'status': 'healthy',     'latency': int(round(p99)),  'error_rate': round(err_rate, 2)},
        'php':        {'status': php_status,    'latency': int(round(p99)),  'error_rate': round(err_rate, 2)},
        'mariadb':    {'status': maria_status,  'latency': int(round(db_p95)), 'error_rate': 0.0},
        'prometheus': {'status': 'healthy',     'latency': 0,                'error_rate': 0.0},
        'locust':     {'status': locust_status, 'latency': 0,                'error_rate': 0.0},
    }


def collect_endpoints() -> list[dict]:
    """
    Per-endpoint table data.

    PERFORMANCE NOTE: the original implementation fired one PromQL query
    PER endpoint PER stat (rate, p95, avg, error rate) = O(N×4) queries.
    Under load with 3 endpoints that's 12 round-trips to Prometheus,
    each adding ~50ms even on localhost. Total: ~600ms — too slow when
    the dashboard polls every 2 seconds.

    This refactor does the same job in exactly 3 batched queries using
    `by(endpoint, method)`. All endpoints are computed in parallel
    server-side by Prometheus. Total: ~50ms regardless of how many
    endpoints exist.
    """
    # Query 1: request rate per (endpoint, method) — main result set
    series = prom_query_all(
        f'sum by(endpoint, method) (rate(nexus_http_requests_total[{RATE_WINDOW}]))'
    )
    if not series:
        return []

    # Query 2: p95 latency per (endpoint, method)
    p95_series = prom_query_all(
        f'histogram_quantile(0.95, sum by(endpoint, method, le) '
        f'(rate(nexus_http_request_duration_seconds_bucket[{RATE_WINDOW}])))'
    )

    # Query 3: avg latency per (endpoint, method) — sum/count division
    avg_series = prom_query_all(
        f'sum by(endpoint, method) (rate(nexus_http_request_duration_seconds_sum[{RATE_WINDOW}]))'
        f' / sum by(endpoint, method) (rate(nexus_http_request_duration_seconds_count[{RATE_WINDOW}]))'
    )

    # Query 4: error rate per endpoint (without method dimension since errors
    # don't carry method label in the metrics_store)
    err_series = prom_query_all(
        f'100 * sum by(endpoint) (rate(nexus_http_errors_total[{RATE_WINDOW}]))'
        f' / sum by(endpoint) (rate(nexus_http_requests_total[{RATE_WINDOW}]))'
    )

    # Index lookups: build dicts keyed by (endpoint, method) for fast joins.
    def index_by_ep_method(series_list):
        out = {}
        for row in series_list:
            ep = row.get('metric', {}).get('endpoint', '/')
            mt = row.get('metric', {}).get('method', 'GET')
            val = row.get('value', [None, None])
            if len(val) > 1:
                out[(ep, mt)] = _safe_float(val[1])
        return out

    def index_by_ep(series_list):
        out = {}
        for row in series_list:
            ep = row.get('metric', {}).get('endpoint', '/')
            val = row.get('value', [None, None])
            if len(val) > 1:
                out[ep] = _safe_float(val[1])
        return out

    p95_map = index_by_ep_method(p95_series)
    avg_map = index_by_ep_method(avg_series)
    err_map = index_by_ep(err_series)

    endpoints = []
    for row in series:
        ep      = row['metric'].get('endpoint', '/')
        method  = row['metric'].get('method', 'GET')
        val = row.get('value', [None, None])
        if len(val) <= 1:
            continue
        rate_rps = _safe_float(val[1])

        p95 = p95_map.get((ep, method), 0.0)
        avg = avg_map.get((ep, method), 0.0)
        err_ep = err_map.get(ep, 0.0)

        avg_ms = int(round(avg * 1000))
        p95_ms = int(round(p95 * 1000))
        cpm    = int(round(rate_rps * 60))

        if err_ep > 10 or p95_ms > 1500:
            status = 'degraded'
        elif err_ep > 2 or p95_ms > 500:
            status = 'warning'
        else:
            status = 'ok'

        endpoints.append({
            'method':     method,
            'path':       ep,
            'latency':    avg_ms,
            'p95':        p95_ms,
            'error_rate': round(err_ep, 1),
            'rpm':        cpm,
            'status':     status,
        })

    endpoints.sort(key=lambda e: e['rpm'], reverse=True)
    return endpoints


def read_scenario_file() -> dict:
    """Read the active scenario file. Returns {'scenario': 'idle'} on miss."""
    try:
        with open(SCENARIO_FILE, 'r') as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {'scenario': 'idle', 'started_at': None}


def write_scenario_file(data: dict) -> bool:
    """Write the active scenario state."""
    try:
        if data.get('scenario') == 'idle':
            # Idle clears the file so /api/scenario semantics stay clean
            if os.path.exists(SCENARIO_FILE):
                os.unlink(SCENARIO_FILE)
            return True
        with open(SCENARIO_FILE, 'w') as f:
            json.dump(data, f)
        return True
    except OSError:
        return False


# ─── HTTP handler ──────────────────────────────────────────────────────

class NexusHandler(http.server.BaseHTTPRequestHandler):

    # Silence the default access log — we don't need it cluttering systemd.
    # Errors still go to stderr.
    def log_message(self, format, *args):
        pass

    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        # CORS: the dashboard may be opened from a different origin while
        # debugging. Apache is on :80, this server on :8081 — different
        # origin per browser definition.
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_preflight(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_OPTIONS(self):
        self._send_cors_preflight()

    def do_GET(self):
        # Parse the URL and query parameters
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == '/api/dashboard_metrics':
            try:
                payload = collect_dashboard_metrics()
                self._send_json(200, payload)
            except Exception as e:
                # Include full traceback in the response and in journald.
                import traceback
                tb = traceback.format_exc()
                print(f'[ERROR /api/dashboard_metrics] {e}\n{tb}', flush=True)
                self._send_json(500, {
                    'error': str(e),
                    'type': type(e).__name__,
                    'traceback': tb.splitlines()[-10:],
                })
            return

        if path == '/api/scenario':
            self._send_json(200, read_scenario_file())
            return

        if path == '/healthz':
            self._send_json(200, {'status': 'ok', 'prom': PROM_URL})
            return

        if path == '/api/logs':
            query_params = urllib.parse.parse_qs(parsed_url.query)
            level = query_params.get('level', ['all'])[0].upper()

            if not docker_client:
                self._send_json(500, [{"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(), "level": "ERROR", "service": "system", "message": "Docker socket not connected."}])
                return

            containers_to_monitor = ['web', 'db']
            logs_output = []

            for service_name in containers_to_monitor:
                try:
                    # Find the running container
                    container_list = docker_client.containers.list(filters={"name": service_name})
                    if not container_list:
                        continue
                        
                    container = container_list[0]
                    # 1. AGGIUNTO: timestamps=True per ottenere l'ora nativa di Docker
                    raw_logs = container.logs(tail=30, stdout=True, stderr=True, timestamps=True).decode('utf-8', errors='replace')
                    
                    for line in raw_logs.splitlines():
                        if not line.strip():
                            continue
                        
                        # 2. Separiamo il timestamp nativo di Docker dal messaggio
                        parts = line.split(" ", 1)
                        if len(parts) == 2 and ("T" in parts[0] or "Z" in parts[0]):
                            log_timestamp = parts[0]
                            log_text = parts[1]
                        else:
                            # Fallback se la riga è strana
                            log_timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
                            log_text = line
                        
                        # 3. Usiamo log_text invece di line per la nostra logica
                        line_upper = log_text.upper()
                        detected_level = "INFO"
                        if "ERROR" in line_upper or "FATAL" in line_upper:
                            detected_level = "ERROR"
                        elif "WARN" in line_upper:
                            detected_level = "WARN"
                        elif "CRIT" in line_upper:
                            detected_level = "CRITICAL"

                        if level != "ALL" and detected_level != level:
                            continue

                        logs_output.append({
                            "timestamp": log_timestamp,
                            "level": detected_level,
                            "service": service_name,
                            "message": log_text[:200] + ("..." if len(log_text) > 200 else "")
                        })
                except Exception as e:
                    print(f"[ERROR] failed reading logs for {service_name}: {e}")

            # Sort the combined logs by timestamp so they appear in order
            logs_output.sort(key=lambda x: x["timestamp"], reverse=True)
            self._send_json(200, logs_output)
            return

        # Fallback 404
        self._send_json(404, {'error': 'not found', 'path': path})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == '/api/scenario':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length) if length > 0 else b'{}'
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._send_json(400, {'error': 'invalid JSON'})
                return

            scenario = data.get('scenario', 'idle')
            if scenario not in ALLOWED_SCENARIOS:
                self._send_json(400, {'error': 'unknown scenario',
                                      'allowed': sorted(ALLOWED_SCENARIOS)})
                return

            payload = {
                'scenario':   scenario,
                'started_at': data.get('started_at'),
                'params':     data.get('params'),
            }
            ok = write_scenario_file(payload)
            self._send_json(200 if ok else 500, payload if ok else {'error': 'write failed'})
            return

        self._send_json(404, {'error': 'not found', 'path': path})


# ─── ThreadingHTTPServer for concurrent requests ───────────────────────
#
# The default HTTPServer handles one request at a time. Under load with
# a polling dashboard + simultaneous scenario notifications from the
# launcher, that becomes a tiny bottleneck on its own. ThreadingMixIn
# spins a thread per request.

class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    # Keep socket open across requests, faster reconnect for the dashboard
    allow_reuse_address = True


def main():
    print(f'NEXUS dashboard server listening on {BIND_HOST}:{BIND_PORT}')
    print(f'  Prometheus: {PROM_URL}')
    print(f'  History:    {RANGE_WINDOW_SEC}s @ step={RANGE_STEP_SEC}s ({HISTORY_POINTS} points)')
    print(f'  Endpoints:  /api/dashboard_metrics, /api/scenario, /healthz')

    with ThreadingHTTPServer((BIND_HOST, BIND_PORT), NexusHandler) as srv:
        srv.serve_forever()


if __name__ == '__main__':
    main()
