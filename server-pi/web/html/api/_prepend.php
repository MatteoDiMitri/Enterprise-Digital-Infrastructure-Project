<?php
/**
 * api/_prepend.php
 *
 * Auto-included request instrumentation loaded at the start of every
 * PHP request via `auto_prepend_file` (Apache) or `php.ini`.
 *
 * What it does:
 * 0) NEW — DDoS rate-limit gate: while the *ddos* scenario is active, any
 *    request beyond NEXUS_RATELIMIT_RPS requests/second is refused fast
 *    with HTTP 503 (recorded in the metrics). This is what makes the
 *    dashboard show the expected DDoS signature: error rate > 2%, 5xx
 *    dominating the donut, availability dropping, SLO -> violation.
 *    It is SCOPED to the ddos scenario on purpose, so saturation and
 *    flash_crowd keep their own signatures (latency knee, 2xx-heavy).
 *    Toggle via NEXUS_RATELIMIT_RPS on the web container (0/unset = off).
 * 1) records request start time
 * 2) increments an in-flight requests counter
 * 3) registers a shutdown handler that measures duration, observes the
 *    latency histogram, updates per-endpoint request/error counters and
 *    decrements the in-flight metric.
 */

declare(strict_types=1);

require_once __DIR__ . '/_metrics_store.php';

// ---------------------------------------------------------------------------
// FILTER: skip requests to /api/* to avoid counting Prometheus scrapes
// ---------------------------------------------------------------------------
$_nexus_skip = false;
$_nexus_uri  = $_SERVER['REQUEST_URI'] ?? '';
if (str_starts_with($_nexus_uri, '/api/')) {
    $_nexus_skip = true;
}

if (!$_nexus_skip) {

    // -----------------------------------------------------------------------
    // (0) DDoS RATE-LIMIT GATE  — produce real server-side 5xx under flood
    // -----------------------------------------------------------------------
    // Rationale: mpm_prefork queues instead of returning 503 on worker
    // exhaustion, and refused connections fail client-side (invisible to
    // these PHP metrics). So without an explicit guard the DDoS scenario
    // never produces a server-side error and error_rate stays ~0%.
    //
    // This guard models a real DDoS mitigation (rate limiting / WAF): when
    // the offered request rate exceeds the configured ceiling, excess
    // requests are shed with 503 BEFORE they touch CPU/DB. The 503s are
    // recorded, so: error rate climbs, the status donut turns red (5xx),
    // availability drops, and php flips to degraded in the topology.
    //
    // Scope: only fires while the active scenario is "ddos" (read from the
    // scenario file the launcher writes). That keeps the mitigation from
    // distorting saturation (which must find its latency knee with no
    // artificial cap) or flash_crowd (which must stay 2xx-heavy).
    //
    // Window: a fixed 1-second counter with a 2s TTL — self-healing, so you
    // do NOT need to restart anything between runs.
    $_nexus_rl = (int) (getenv('NEXUS_RATELIMIT_RPS') ?: 0);
    if ($_nexus_rl > 0) {
        $sec  = (int) floor(microtime(true));
        $hits = _nexus_rl_hit($sec);
        if ($hits > $_nexus_rl) {
            http_response_code(503);
            header('Retry-After: 1');
            header('Content-Type: application/json; charset=utf-8');

            // Record the shed request here (and exit) — we never register
            // the normal shutdown handler on this path, so count it now.
            $endpoint = self_nexus_normalize_endpoint($_nexus_uri);
            $method   = $_SERVER['REQUEST_METHOD'] ?? 'GET';
            MetricsStore::inc('nexus_http_requests_total', [
                'endpoint' => $endpoint,
                'method'   => $method,
                'status'   => '503',
            ]);
            MetricsStore::inc('nexus_http_errors_total', [
                'type'     => '5xx',
                'status'   => '503',
                'endpoint' => $endpoint,
            ]);

            // AVAILABILITY FIX: also record the shed request in the latency
            // histogram. The dashboard computes
            //     availability = (within_slo - errors) / total
            // where `within_slo` is the count of requests in the le<=1s
            // histogram bucket. Without this line the 503 is counted only as
            // an error and NEVER enters the histogram, so it is subtracted
            // from `within_slo` without ever having been added to it -> a
            // double penalty that drives availability to ~(100 - 2*err%)
            // (e.g. 21.5% err showed 57% instead of the honest ~78.5%).
            //
            // The shed happens before the app runs, so the response is
            // effectively instant (~1ms) and lands in the smallest bucket.
            // Now the 503 appears in BOTH `within_slo` and `errors`, the two
            // cancel in the numerator, and availability reads the true
            // success-and-fast fraction. Side effect: fast 503 samples enter
            // the latency distribution, so P50 dips slightly; P95/P99 (the
            // SLO percentiles, set by the slow 2xx tail) are unaffected.
            MetricsStore::observe(
                'nexus_http_request_duration_seconds',
                0.001,
                NEXUS_HTTP_BUCKETS,
                ['endpoint' => $endpoint, 'method' => $method]
            );

            echo '{"error":"service unavailable","reason":"rate limited"}';
            exit; // do NOT run the app, do NOT count in-flight, do NOT register shutdown
        }
    }

    // -----------------------------------------------------------------------
    // REQUEST START
    // -----------------------------------------------------------------------
    $GLOBALS['_nexus_t0']  = microtime(true);
    $GLOBALS['_nexus_uri'] = $_nexus_uri;
    $GLOBALS['_nexus_method'] = $_SERVER['REQUEST_METHOD'] ?? 'GET';

    // Increment the in-flight requests metric.
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

            $endpoint = self_nexus_normalize_endpoint($uri);

            // Counter: total requests per endpoint+method+status
            MetricsStore::inc('nexus_http_requests_total', [
                'endpoint' => $endpoint,
                'method'   => $method,
                'status'   => (string)$status,
            ]);

            // Histogram: latency distribution per endpoint+method
            MetricsStore::observe(
                'nexus_http_request_duration_seconds',
                $elapsed,
                NEXUS_HTTP_BUCKETS,
                ['endpoint' => $endpoint, 'method' => $method]
            );

            // Errors: counted separately to make error rate queries easier
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
            error_log('[nexus-prepend] shutdown handler error: ' . $e->getMessage());
        }
    });
}

/**
 * Return the currently active scenario name ("idle" if none), cached in
 * APCu for ~1s so the per-request file read under load is negligible.
 * Reads the same file the launcher writes via api/scenario.php.
 */
function _nexus_active_scenario(): string
{
    $apcu = function_exists('apcu_enabled') && apcu_enabled();
    if ($apcu) {
        $cached = apcu_fetch('nexus_rl_scn', $ok);
        if ($ok && is_string($cached)) {
            return $cached;
        }
    }
    $scn  = 'idle';
    $file = '/tmp/nexus_active_scenario.json';
    if (is_file($file)) {
        $raw = @file_get_contents($file);
        if ($raw) {
            $j = json_decode($raw, true);
            if (is_array($j) && !empty($j['scenario'])) {
                $scn = (string) $j['scenario'];
            }
        }
    }
    if ($apcu) {
        apcu_store('nexus_rl_scn', $scn, 1); // 1s TTL
    }
    return $scn;
}

/**
 * Fixed-window request counter for the given 1-second bucket. Returns the
 * running count for that second. APCu path uses a 2s TTL so old buckets
 * expire on their own (no drift, no manual reset). Filesystem fallback is
 * best-effort and only used when APCu is unavailable.
 */
function _nexus_rl_hit(int $sec): int
{
    $key = 'nexus_rl:' . $sec;
    if (function_exists('apcu_enabled') && apcu_enabled()) {
        $n = apcu_inc($key, 1, $ok, 2); // create with 2s TTL if missing
        if ($n === false || !$ok) {
            apcu_store($key, 1, 2);
            return 1;
        }
        return (int) $n;
    }
    // Filesystem fallback (best-effort).
    $f = '/tmp/nexus_rl_' . $sec . '.cnt';
    $n = 1;
    $fp = @fopen($f, 'c+');
    if ($fp !== false) {
        if (flock($fp, LOCK_EX)) {
            $cur = (int) stream_get_contents($fp);
            $n = $cur + 1;
            ftruncate($fp, 0);
            rewind($fp);
            fwrite($fp, (string) $n);
            fflush($fp);
            flock($fp, LOCK_UN);
        }
        fclose($fp);
        // opportunistic cleanup of the previous second's file
        @unlink('/tmp/nexus_rl_' . ($sec - 2) . '.cnt');
    }
    return $n;
}

/**
 * Normalize the URI: strip query string and collapse all-numeric path
 * segments to {id} to bound Prometheus series cardinality.
 *   /index.php?product_id=42  ->  /index.php
 *   /products/12345/edit      ->  /products/{id}/edit
 */
function self_nexus_normalize_endpoint(string $uri): string
{
    $q = strpos($uri, '?');
    if ($q !== false) {
        $uri = substr($uri, 0, $q);
    }
    $segments = explode('/', $uri);
    foreach ($segments as &$seg) {
        if ($seg !== '' && ctype_digit($seg)) {
            $seg = '{id}';
        }
    }
    unset($seg);
    return implode('/', $segments) ?: '/';
}