<?php
/**
 * 404.php
 *
 * Custom "Not Found" handler, wired in via `ErrorDocument 404 /404.php`
 * (.htaccess). Its whole reason to exist is observability: Apache's
 * default 404 page is served WITHOUT touching PHP, so 4xx traffic never
 * reached the metrics pipeline and the dashboard's error rate / donut /
 * endpoint table stayed empty.
 *
 * How the metric gets recorded
 * ----------------------------
 * On mod_php the auto-prepend instrumentation (api/_prepend.php) runs for
 * this internal sub-request as well. It reads the ORIGINAL request URI
 * (e.g. /this-page-does-not-exist) and the response code we set below
 * (404), so it records the request and the error on shutdown — one clean
 * count, no extra work needed here.
 *
 * Fallback: if for some reason the prepend did NOT run for this error
 * document (e.g. a PHP-FPM setup without auto_prepend on error docs),
 * `$GLOBALS['_nexus_t0']` will be unset and we record the metric here
 * instead. The guard guarantees we never double-count.
 */

declare(strict_types=1);

// Make sure the response really goes out as 404 (and not 200).
http_response_code(404);

if (!isset($GLOBALS['_nexus_t0'])) {
    // Prepend instrumentation did not run for this request — record here.
    require_once __DIR__ . '/api/_metrics_store.php';

    // Original requested path. On an ErrorDocument internal redirect Apache
    // exposes it via REDIRECT_URL; fall back to REQUEST_URI.
    $uri = $_SERVER['REDIRECT_URL'] ?? ($_SERVER['REQUEST_URI'] ?? '/');

    // Same normalization rule as _prepend.php: strip query string and
    // collapse all-numeric path segments to {id} to bound cardinality.
    if (($q = strpos($uri, '?')) !== false) {
        $uri = substr($uri, 0, $q);
    }
    $segments = explode('/', $uri);
    foreach ($segments as &$seg) {
        if ($seg !== '' && ctype_digit($seg)) {
            $seg = '{id}';
        }
    }
    unset($seg);
    $endpoint = implode('/', $segments) ?: '/';
    $method   = $_SERVER['REQUEST_METHOD'] ?? 'GET';

    MetricsStore::inc('nexus_http_requests_total', [
        'endpoint' => $endpoint,
        'method'   => $method,
        'status'   => '404',
    ]);
    MetricsStore::inc('nexus_http_errors_total', [
        'type'     => '4xx',
        'status'   => '404',
        'endpoint' => $endpoint,
    ]);
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>404 — Not Found · NEXUS</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; background: #f5f2ec;
           color: #1a1714; display: flex; min-height: 100vh; align-items: center;
           justify-content: center; margin: 0; }
    .box { text-align: center; }
    .code { font-family: ui-monospace, monospace; color: #c84b2f; font-size: 12px;
            letter-spacing: 0.15em; text-transform: uppercase; margin-bottom: 0.75rem; }
    h1 { font-size: 2rem; margin: 0 0 0.5rem; }
    p { color: #7a7570; font-size: 14px; margin: 0; }
  </style>
</head>
<body>
  <div class="box">
    <div class="code">NEXUS · 404</div>
    <h1>Page not found</h1>
    <p>The page you requested does not exist.</p>
  </div>
</body>
</html>
