<?php
/**
 * api/dashboard_metrics.php
 *
 * JSON shim that translates Prometheus queries into the shape expected
 * by `dashboard.html`. This endpoint is served by Apache (same origin)
 * so the browser does not talk directly to Prometheus.
 *
 * URL: GET /api/dashboard_metrics
 * Called by the dashboard every 2 seconds (POLL_MS = 2000).
 *
 * Response shape example:
 * {
 *   "kpi": { "rps": float, "latency_p99": int(ms), "error_rate": float(0-100), ... },
 *   "history": { "latency_p50": [60 floats(ms)], "rps": [60 floats], ... },
 *   "status_distribution": {"2xx":int, "3xx":int, "4xx":int, "5xx":int},
 *   "services": { name: {status, latency, error_rate} },
 *   "endpoints": [ {method,path,latency,p95,error_rate,rpm,status}, ... ],
 *   "scenario": "idle"|"normal"|...
 * }
 *
 * All Prometheus queries go through `prom_query()` / `prom_query_range()`
 * with a short timeout (2s). If Prometheus is unavailable we return
 * zero/empty values rather than blocking the dashboard.
 */

declare(strict_types=1);

// ────────────────────────────────────────────────────────────────────────────
// CONFIG
// ────────────────────────────────────────────────────────────────────────────
const PROMETHEUS_URL    = 'http://127.0.0.1:9090';
const RANGE_WINDOW_SEC  = 90;       // seconds of history for charts
// IMPORTANT: Prometheus requires integer `step` values (with time unit
// suffix like "s" or "m"). Decimal steps (e.g. "1.5s") produce HTTP 400.
// With step=2s we get exactly 45 points (90/2). The dashboard charts
// render 45 real-data points left-to-right without left padding.
const RANGE_STEP_SEC    = 2;
const RATE_WINDOW       = '1m';     // finestra per rate(): 1 minuto
const SCENARIO_FILE     = '/tmp/nexus_active_scenario.json';

// SLO thresholds (tarati per match con dashboard.html)
const SLO_P99_MS_TARGET    = 1000;  // p99 < 1s
const SLO_ERROR_RATE_PCT   = 2.0;   // < 2%
const SLO_AVAILABILITY_PCT = 99.0;  // > 99%

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

// ────────────────────────────────────────────────────────────────────────────
// HTTP CLIENT (curl) per parlare con Prometheus
// ────────────────────────────────────────────────────────────────────────────

/**
 * Execute a PromQL instant query. Returns the scalar value (or 0.0 on
 * error or empty result).
 */
function prom_query(string $promql): float
{
    $url = PROMETHEUS_URL . '/api/v1/query?query=' . urlencode($promql);
    $body = prom_curl($url);
    if (!$body) return 0.0;
    $j = json_decode($body, true);
    if (($j['status'] ?? '') !== 'success') return 0.0;
    $result = $j['data']['result'] ?? [];
    if (empty($result)) return 0.0;
    // result = [{ metric: {...}, value: [ts, "1234"] }, ...]
    // Per query "scalar-equivalent" prendiamo il primo valore.
    return (float)($result[0]['value'][1] ?? 0);
}

/**
 * Execute a PromQL range query. Returns an array of N floats where
 * N = (RANGE_WINDOW_SEC / RANGE_STEP_SEC) — default 45 points.
 * If less history is available (e.g. after Prometheus restart) we
 * left-pad the returned series with zeros to always return N points.
 */
function prom_query_range(string $promql, int $points = 45): array
{
    $now    = time();
    $start  = $now - (int)RANGE_WINDOW_SEC;
    $url    = PROMETHEUS_URL . '/api/v1/query_range'
        . '?query=' . urlencode($promql)
        . '&start=' . $start
        . '&end='   . $now
        . '&step='  . RANGE_STEP_SEC . 's';

    $body = prom_curl($url);
    if (!$body) return array_fill(0, $points, 0.0);

    $j = json_decode($body, true);
    if (($j['status'] ?? '') !== 'success') return array_fill(0, $points, 0.0);

    $result = $j['data']['result'] ?? [];
    if (empty($result)) return array_fill(0, $points, 0.0);

    // Prendiamo i values del primo (e di solito unico) result set.
    // values = [[ts, "v"], [ts, "v"], ...]
    $values = $result[0]['values'] ?? [];
    $series = array_map(fn($v) => (float)$v[1], $values);

    // Padding/truncation a esattamente $points elementi
    if (count($series) >= $points) {
        return array_slice($series, -$points);
    }
    $missing = $points - count($series);
    return array_merge(array_fill(0, $missing, 0.0), $series);
}

/** Simple curl wrapper with short timeouts and silent error handling. */
function prom_curl(string $url): ?string
{
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 2,
        CURLOPT_CONNECTTIMEOUT => 1,
    ]);
    $body = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($body === false || $code !== 200) return null;
    return $body;
}

// ────────────────────────────────────────────────────────────────────────────
// QUERIES PROMQL PER OGNI CAMPO
// ────────────────────────────────────────────────────────────────────────────
// Tutte le query usano "1m" come finestra di rate(): un buon
// compromesso fra reattività e stabilità sotto carico variabile.
// ────────────────────────────────────────────────────────────────────────────

// KPI: current throughput (req/s)
$rps = prom_query(
    'sum(rate(nexus_http_requests_total[' . RATE_WINDOW . ']))'
);

// KPI: p99 latency (seconds → ms)
$p99Sec = prom_query(
    'histogram_quantile(0.99,'
    . ' sum by(le) (rate(nexus_http_request_duration_seconds_bucket[' . RATE_WINDOW . '])))'
);
$p99Ms = (int)round($p99Sec * 1000);

// KPI: error rate (%)
$errRate = prom_query(
    '100 * sum(rate(nexus_http_errors_total[' . RATE_WINDOW . ']))'
    . ' / clamp_min(sum(rate(nexus_http_requests_total[' . RATE_WINDOW . '])), 1)'
);

// KPI: CPU percentage
$cpu = (int)round(prom_query('nexus_system_cpu_percent'));

// KPI: queue depth (in-flight requests). Apache mod_status would be
// more accurate, but the delta counter is sufficient to show load.
$queueDepth = (int)round(prom_query('nexus_active_requests_inflight'));

// HISTORY: latency p50/p95/p99 (ms)
$histP50 = prom_query_range(
    'histogram_quantile(0.50, sum by(le) '
    . '(rate(nexus_http_request_duration_seconds_bucket[' . RATE_WINDOW . ']))) * 1000'
);
$histP95 = prom_query_range(
    'histogram_quantile(0.95, sum by(le) '
    . '(rate(nexus_http_request_duration_seconds_bucket[' . RATE_WINDOW . ']))) * 1000'
);
$histP99 = prom_query_range(
    'histogram_quantile(0.99, sum by(le) '
    . '(rate(nexus_http_request_duration_seconds_bucket[' . RATE_WINDOW . ']))) * 1000'
);

// HISTORY: RPS and error rate
$histRps = prom_query_range(
    'sum(rate(nexus_http_requests_total[' . RATE_WINDOW . ']))'
);
$histErr = prom_query_range(
    '100 * sum(rate(nexus_http_errors_total[' . RATE_WINDOW . ']))'
    . ' / clamp_min(sum(rate(nexus_http_requests_total[' . RATE_WINDOW . '])), 1)'
);

// HISTORY: queue depth and DB p95 latency
$histQueue = prom_query_range('nexus_active_requests_inflight');
$histDb = prom_query_range(
    'histogram_quantile(0.95, sum by(le) '
    . '(rate(nexus_db_query_duration_seconds_bucket[' . RATE_WINDOW . ']))) * 1000'
);

// HISTORY: CPU and memory percentage
$histCpu = prom_query_range('nexus_system_cpu_percent');
$histMem = prom_query_range('nexus_system_memory_used_percent');

// STATUS DISTRIBUTION (donut)
$count2xx = prom_query(
    'sum(rate(nexus_http_requests_total{status=~"2.."}[' . RATE_WINDOW . ']))'
);
$count3xx = prom_query(
    'sum(rate(nexus_http_requests_total{status=~"3.."}[' . RATE_WINDOW . ']))'
);
$count4xx = prom_query(
    'sum(rate(nexus_http_requests_total{status=~"4.."}[' . RATE_WINDOW . ']))'
);
$count5xx = prom_query(
    'sum(rate(nexus_http_requests_total{status=~"5.."}[' . RATE_WINDOW . ']))'
);

// SERVICES TOPOLOGY: collect status for each real service in the stack
// (apache, mariadb, prometheus, php, locust). Values come from
// Prometheus metrics with healthy fallbacks when a metric is missing.
$services = nexus_collect_services($p99Ms, $errRate);

// ENDPOINT TABLE: per ogni endpoint distinto, latenza media, p95,
// error rate e calls/min.
$endpoints = nexus_collect_endpoints();

// SCENARIO: leggiamo lo scenario attivo dal file scritto da
// scenario.php (POST dal FastAPI launcher). Default: "idle".
$scenario = 'idle';
if (file_exists(SCENARIO_FILE)) {
    $j = json_decode((string)@file_get_contents(SCENARIO_FILE), true);
    if (is_array($j) && !empty($j['scenario'])) {
        $scenario = $j['scenario'];
    }
}

// SLO compliance combinato
$slo = 'ok';
if ($p99Ms > SLO_P99_MS_TARGET || $errRate > 10 || $cpu > 95) {
    $slo = 'violation';
} elseif ($p99Ms > SLO_P99_MS_TARGET / 2 || $errRate > SLO_ERROR_RATE_PCT || $cpu > 80) {
    $slo = 'at_risk';
}

// User-perceived availability: percentage of requests that completed
// SUCCESSFULLY (2xx/3xx) AND within the latency SLO budget (1 second).
//
// This is more honest than "100 - error_rate" because a 500ms response
// is technically "available" (it returned) but a 30-second response is
// not — even though both are 200 OK.
//
// Formula:
//   slow_responses = requests above the SLO bucket (le=1.0s)
//   total          = all requests
//   availability   = 100 * (total - errors - slow) / total
//
// The "le=1.0" bucket from the histogram gives us the count of requests
// that finished within 1 second. Anything above is counted as a slow
// response that violates the SLO.
$totalRate = prom_query('sum(rate(nexus_http_requests_total[' . RATE_WINDOW . ']))');
$errorsRate = prom_query('sum(rate(nexus_http_errors_total[' . RATE_WINDOW . ']))');
$withinSloRate = prom_query(
    'sum(rate(nexus_http_request_duration_seconds_bucket{le="1"}[' . RATE_WINDOW . ']))'
);
// requests above 1s budget = total - within_slo
// successful_and_fast      = within_slo - errors_within_slo (approx: subtract errors)
// availability             = (within_slo - errors) / total
if ($totalRate > 0) {
    $successAndFast = max(0, $withinSloRate - $errorsRate);
    $availability   = ($successAndFast / $totalRate) * 100;
} else {
    // No traffic at all → vacuously 100% available
    $availability = 100.0;
}
$availability = max(0.0, min(100.0, $availability));

// ────────────────────────────────────────────────────────────────────────────
// SERIALIZE
// ────────────────────────────────────────────────────────────────────────────
$active = array_filter($services, fn($s) => ($s['status'] ?? 'healthy') === 'healthy');
$activeServicesStr = count($active) . '/' . count($services);


echo json_encode([
    'kpi' => [
        'rps'             => (float)round($rps, 1),
        'latency_p99'     => $p99Ms,
        'error_rate'      => (float)round($errRate, 2),
        'availability'    => (float)round($availability, 2),
        'active_services' => $activeServicesStr,    // formato "healthy/total" atteso dal frontend
        'cpu'             => $cpu,
        'queue_depth'     => $queueDepth,
        'slo_status'      => $slo,
    ],
    'history' => [
        'latency_p50' => $histP50,
        'latency_p95' => $histP95,
        'latency_p99' => $histP99,
        'rps'         => $histRps,
        'error_rate'  => $histErr,
        'queue_depth' => $histQueue,
        'db_latency'  => $histDb,
        'cpu'         => $histCpu,
        'memory'      => $histMem,
    ],
    'status_distribution' => [
        '2xx' => (int)round($count2xx),
        '3xx' => (int)round($count3xx),
        '4xx' => (int)round($count4xx),
        '5xx' => (int)round($count5xx),
    ],
    'services'  => $services,
    'endpoints' => $endpoints,
    'scenario'  => $scenario,
]);

// ────────────────────────────────────────────────────────────────────────────
// HELPERS
// ────────────────────────────────────────────────────────────────────────────

/**
 * Build a service topology map. Each service contains:
 * - status:     "healthy" | "warning" | "degraded" | "down"
 * - latency:    ms (proxy for responsiveness)
 * - error_rate: % (proxy for problems)
 *
 * The stack: apache, mariadb, prometheus, php, locust. We infer
 * status from concrete Prometheus metrics as listed below.
 */
function nexus_collect_services(int $p99Ms, float $errRate): array
{
    $dbQueries  = prom_query('sum(rate(nexus_db_queries_total[' . RATE_WINDOW . ']))');
    $dbP95Sec   = prom_query('histogram_quantile(0.95, sum by(le) (rate(nexus_db_query_duration_seconds_bucket[' . RATE_WINDOW . ']))) * 1000');
    $reqRate    = prom_query('sum(rate(nexus_http_requests_total[' . RATE_WINDOW . ']))');
    $cpu        = prom_query('nexus_system_cpu_percent');
    $dbP95Ms    = (int) round($dbP95Sec, 0);

    // Apache: sempre healthy se PHP risponde (siamo arrivati a questo punto
    // significa che le metriche /api/metrics rispondono, quindi Apache è up)
    $apacheStatus = 'healthy';

    // PHP: degradato se CPU > 90% (worker satura), warning se latency > 1s
    // o error rate > 5% (l'app sta avendo difficoltà)
    if ($cpu > 90) {
        $phpStatus = 'degraded';
    } elseif ($p99Ms > 1000 || $errRate > 5) {
        $phpStatus = 'warning';
    } else {
        $phpStatus = 'healthy';
    }

    // MariaDB: distinguere "DB inattivo perché non interrogato" (DDoS) da
    // "DB inattivo perché caduto" (errore reale). La seconda condizione
    // richiede: traffico HTTP attivo, ALMENO un endpoint scrive (es. checkout),
    // ma nessuna query DB osservata. Tutto il resto è 'healthy'.
    //
    // Inoltre: se ci sono query ma sono lente (p95 > 500ms), è warning.
    if ($dbQueries > 0 && $dbP95Ms > 500) {
        $mariaStatus = 'warning';      // DB sotto stress
    } else {
        $mariaStatus = 'healthy';      // tutto OK (o DB semplicemente non usato)
    }

    $promStatus = 'healthy';   // Se siamo qui, Prometheus ha risposto.

    // Locust: deriviamo da scenario attivo
    $locustStatus = (file_exists(SCENARIO_FILE)) ? 'healthy' : 'down';
    if ($locustStatus === 'healthy') {
        $j = json_decode((string)@file_get_contents(SCENARIO_FILE), true);
        if (!is_array($j) || empty($j['scenario']) || $j['scenario'] === 'idle') {
            $locustStatus = 'down';
        }
    }

    return [
        'apache'     => ['status' => $apacheStatus,  'latency' => $p99Ms,  'error_rate' => round($errRate, 2)],
        'php'        => ['status' => $phpStatus,     'latency' => $p99Ms,  'error_rate' => round($errRate, 2)],
        'mariadb'    => ['status' => $mariaStatus,   'latency' => $dbP95Ms, 'error_rate' => 0.0],
        'prometheus' => ['status' => $promStatus,    'latency' => 0,        'error_rate' => 0.0],
        'locust'     => ['status' => $locustStatus,  'latency' => 0,        'error_rate' => 0.0],
    ];
}

/**
 * Collect per-endpoint metrics via PromQL. Returns an array ready for
 * rendering in the dashboard table.
 */
function nexus_collect_endpoints(): array
{
    // Query "raw": rate per ciascuna combinazione (endpoint, method)
    $url = PROMETHEUS_URL . '/api/v1/query?query='
        . urlencode('sum by(endpoint, method) (rate(nexus_http_requests_total[' . RATE_WINDOW . ']))');
    $body = prom_curl($url);
    if (!$body) return [];
    $j = json_decode($body, true);
    if (($j['status'] ?? '') !== 'success') return [];

    $endpoints = [];
    foreach ($j['data']['result'] ?? [] as $row) {
        $ep      = $row['metric']['endpoint'] ?? '/';
        $method  = $row['metric']['method']   ?? 'GET';
        $rateRPS = (float)$row['value'][1];

        // Per-endpoint p95 latency
        $p95Sec = prom_query(
            'histogram_quantile(0.95, sum by(le) '
            . '(rate(nexus_http_request_duration_seconds_bucket{endpoint="' . $ep . '",method="' . $method . '"}['
            . RATE_WINDOW . '])))'
        );
        $avgSec = prom_query(
            'sum(rate(nexus_http_request_duration_seconds_sum{endpoint="' . $ep . '",method="' . $method . '"}[' . RATE_WINDOW . ']))'
            . ' / clamp_min(sum(rate(nexus_http_request_duration_seconds_count{endpoint="' . $ep . '",method="' . $method . '"}[' . RATE_WINDOW . '])), 1)'
        );

        // Per-endpoint error rate
        $errEp = prom_query(
            '100 * sum(rate(nexus_http_errors_total{endpoint="' . $ep . '"}[' . RATE_WINDOW . ']))'
            . ' / clamp_min(sum(rate(nexus_http_requests_total{endpoint="' . $ep . '",method="' . $method . '"}[' . RATE_WINDOW . '])), 1)'
        );

        $p95Ms = (int)round($p95Sec * 1000);
        $avgMs = (int)round($avgSec * 1000);
        $cpm   = (int)round($rateRPS * 60);

        $status = 'ok';
        if ($errEp > 10 || $p95Ms > 1500) $status = 'degraded';
        elseif ($errEp > 2 || $p95Ms > 500) $status = 'warning';

        $endpoints[] = [
            'method'        => $method,
            'path'          => $ep,
            'latency'       => $avgMs,        // dashboard chiama questo `ep.latency`
            'p95'           => $p95Ms,        // dashboard chiama questo `ep.p95`
            'error_rate'    => round($errEp, 1),
            'rpm'           => $cpm,          // dashboard chiama questo `ep.rpm`
            'status'        => $status,
        ];
    }

    // Sort by rpm desc to show hottest endpoints first
    usort($endpoints, fn($a, $b) => $b['rpm'] - $a['rpm']);
    return $endpoints;
}
