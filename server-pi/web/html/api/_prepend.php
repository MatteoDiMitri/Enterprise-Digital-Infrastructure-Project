<?php
/**
 * api/_prepend.php
 *
 * Auto-included request instrumentation loaded at the start of every
 * PHP request via `auto_prepend_file` (Apache) or `php.ini`.
 *
 * What it does:
 * 1) records request start time
 * 2) increments an in-flight requests counter
 * 3) registers a shutdown handler that measures total duration,
 *    observes the latency histogram, updates per-endpoint request
 *    counters and error counters, and decrements the in-flight metric.
 *
 * Advantages:
 * - No changes required in application files (index.php, checkout.php,
 *   db.php) aside from enabling the DB wrapper. All requests are
 *   instrumented centrally.
 *
 * Safety:
 * - The shutdown handler traps exceptions and never exposes errors to
 *   application output. It also ignores Prometheus scrapes to avoid
 *   recursion and poll noise.
 */

declare(strict_types=1);

// Carica lo store. require_once è importante: il prepend può essere
// chiamato due volte in scenari esotici (sub-richieste), evitiamo doppi
// caricamenti.
require_once __DIR__ . '/_metrics_store.php';

// ---------------------------------------------------------------------------
// FILTER: skip requests to /api/* to avoid counting Prometheus scrapes
// ---------------------------------------------------------------------------
// Prometheus scrapes /api/metrics every 5s. Counting those scrapes would:
// - pollute RPS/traffic metrics with artificial requests
// - risk exposure loops (Prometheus scraping metrics it is still collecting)
//
// The same applies to /api/system_metrics and /api/dashboard_metrics.
// We skip the entire /api/ prefix here.
$_nexus_skip = false;
$_nexus_uri  = $_SERVER['REQUEST_URI'] ?? '';
if (str_starts_with($_nexus_uri, '/api/')) {
    $_nexus_skip = true;
}

if (!$_nexus_skip) {
    // -----------------------------------------------------------------------
    // REQUEST START
    // -----------------------------------------------------------------------
    $GLOBALS['_nexus_t0']  = microtime(true);
    $GLOBALS['_nexus_uri'] = $_nexus_uri;
    $GLOBALS['_nexus_method'] = $_SERVER['REQUEST_METHOD'] ?? 'GET';

    // Increment the in-flight requests metric. APCu lacks native atomic
    // gauge inc/dec, so we emulate it with a delta counter (apcu_inc
    // accepts negative increments).
    MetricsStore::inc('nexus_active_requests_inflight', [], +1);

    // -----------------------------------------------------------------------
    // SHUTDOWN HANDLER
    // -----------------------------------------------------------------------
    register_shutdown_function(function () {
        try {
            $t0     = $GLOBALS['_nexus_t0']     ?? null;
            $uri    = $GLOBALS['_nexus_uri']    ?? '';
            $method = $GLOBALS['_nexus_method'] ?? 'GET';
            if ($t0 === null) return;

            $elapsed = microtime(true) - $t0;
            $status  = http_response_code() ?: 200;

            // Normalize endpoint to avoid high cardinality:
            //   /index.php?product_id=42  -> /index.php
            //   /products/12345/edit      -> /products/{id}/edit
            $endpoint = self_nexus_normalize_endpoint($uri);

            // Counter: total requests per endpoint+method+status
            MetricsStore::inc('nexus_http_requests_total', [
                'endpoint' => $endpoint,
                'method'   => $method,
                'status'   => (string)$status,
            ]);

            // Histogram: latency distribution per endpoint+method
            // (exclude status so percentiles are meaningful across outcomes)
            MetricsStore::observe(
                'nexus_http_request_duration_seconds',
                $elapsed,
                NEXUS_HTTP_BUCKETS,
                ['endpoint' => $endpoint, 'method' => $method]
            );

            // Errors: counted separately to make error rate queries easier
            // (rate(nexus_http_errors_total) is easier than filtering the
            // request counter by status regex in many queries).
            if ($status >= 400) {
                $errType = ($status >= 500) ? '5xx' : '4xx';
                MetricsStore::inc('nexus_http_errors_total', [
                    'type'     => $errType,
                    'status'   => (string)$status,
                    'endpoint' => $endpoint,
                ]);
            }

            // Decrement in-flight requests
            MetricsStore::inc('nexus_active_requests_inflight', [], -1);
        } catch (\Throwable $e) {
            // SILENT FAIL: la strumentazione non deve mai sporcare
            // l'output dell'applicazione. Log a syslog/error_log per
            // debug se serve.
            error_log('[nexus-prepend] shutdown handler error: ' . $e->getMessage());
        }
    });
}

/**
 * Normalizza l'URI rimuovendo query string e identificativi numerici
 * dal path. Senza questa cosa avremmo migliaia di series uniche
 * (/index.php?product_id=1, ...=2, ...=3...) che fanno esplodere
 * la cardinalità in Prometheus.
 *
 * Regole:
 *   /index.php?product_id=42  →  /index.php
 *   /api/foo                  →  /api/foo (ignorato a monte comunque)
 *   /                         →  /
 *   /products/12345/edit      →  /products/{id}/edit
 */
function self_nexus_normalize_endpoint(string $uri): string
{
    // 1. Rimuovi query string
    $q = strpos($uri, '?');
    if ($q !== false) {
        $uri = substr($uri, 0, $q);
    }
    // 2. Sostituisci segmenti tutti-numerici con {id}
    $segments = explode('/', $uri);
    foreach ($segments as &$seg) {
        if ($seg !== '' && ctype_digit($seg)) {
            $seg = '{id}';
        }
    }
    unset($seg);
    return implode('/', $segments) ?: '/';
}
