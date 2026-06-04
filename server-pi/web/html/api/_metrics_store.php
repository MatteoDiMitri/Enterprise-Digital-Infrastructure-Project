<?php
/**
 * api/_metrics_store.php
 *
 * Shared metrics storage used by PHP processes. PHP requests are
 * isolated per-process (mod_php) or per-worker (PHP-FPM), so global
 * variables do not persist across requests. This module provides a
 * cross-process store and atomic operations for counters and histograms.
 *
 * Implementation choices:
 * - APCu (preferred): fast shared-memory operations (apcu_inc/apcu_fetch).
 * - Filesystem + flock (fallback): writes a JSON file under /tmp.
 *
 * Data model:
 * - counter:   monotonic integer (inc only)
 * - gauge:     settable float
 * - histogram: buckets + sum + count (observed values)
 *
 * Labels are sorted and serialized into the key to guarantee
 * deterministic atomic updates without multiple locks.
 */

declare(strict_types=1);

// ---------------------------------------------------------------------------
// CONSTANTS
// ---------------------------------------------------------------------------

/** Fallback storage file path. /tmp is writable by www-data on Apache. */
const NEXUS_METRICS_FILE = '/tmp/nexus_metrics.json';

/** HTTP latency histogram buckets in seconds.
 *  Cover ~5ms → 5s. Extra resolution between 100ms and 1.5s because that
 *  is exactly the band our scenarios live in: with coarse buckets a few
 *  slow requests made histogram_quantile leap (e.g. 500ms straight to
 *  1000+ms p99). Finer steps there make the percentile curve smooth and
 *  faithful instead of jumpy. NOTE: changing buckets starts new Prometheus
 *  series — do a fresh run / restart Prometheus after editing. */
const NEXUS_HTTP_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.5, 5.0];

/** DB query latency histogram buckets. Narrower since queries are
 * expected to be fast; when DB degrades they spike. */
const NEXUS_DB_BUCKETS = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5];

// ---------------------------------------------------------------------------
// METRICS STORE
// ---------------------------------------------------------------------------

final class MetricsStore
{
    /** @var bool|null Cache for APCu detection (null = not yet detected). */
    private static ?bool $apcuAvailable = null;

    /**
     * Detect if APCu is available and enabled (cached after first call).
     */
    public static function hasApcu(): bool
    {
        if (self::$apcuAvailable !== null) {
            return self::$apcuAvailable;
        }
        // function_exists alone is not sufficient: APCu may be installed
        // but disabled in the current SAPI. Also verify the extension is
        // enabled by calling apcu_enabled(). Cache the result.
        self::$apcuAvailable = function_exists('apcu_enabled') && apcu_enabled();
        return self::$apcuAvailable;
    }

    /**
     * Build a storage key from metric name + labels. Labels are sorted to
     * ensure idempotence: the same label set produces the same key.
     */
    private static function key(string $name, array $labels = []): string
    {
        if (empty($labels)) {
            return $name;
        }
        ksort($labels);
        $parts = [];
        foreach ($labels as $k => $v) {
            // Escape label values to avoid breaking Prometheus exposition
            // format. Replace backslash, newline and double-quote.
            $v = str_replace(['\\', "\n", '"'], ['\\\\', '\\n', '\\"'], (string)$v);
            $parts[] = $k . '=' . $v;
        }
        return $name . '{' . implode(',', $parts) . '}';
    }

    // ───────────────────────────────────────────────────────────── COUNTER ──

    /**
     * Atomically increment a counter.
     *
     * @param string $name   Metric name (e.g. "nexus_http_requests_total")
     * @param array  $labels Key/value label pairs (e.g. ['endpoint'=>'/','method'=>'GET'])
     * @param int    $by     Increment amount (default 1)
     */
    public static function inc(string $name, array $labels = [], int $by = 1): void
    {
        $key = 'c:' . self::key($name, $labels);

        if (self::hasApcu()) {
            // apcu_inc è atomico anche senza CAS lock esplicito.
            // Se la chiave non esiste, viene creata con valore $by.
            apcu_inc($key, $by, $success, 0);
            return;
        }
        self::withFileLock(function (array &$data) use ($key, $by) {
            $data[$key] = ($data[$key] ?? 0) + $by;
        });
    }

    // ───────────────────────────────────────────────────────────── GAUGE ────

    /**
     * Set a gauge to an absolute float value.
     */
    public static function setGauge(string $name, float $value, array $labels = []): void
    {
        $key = 'g:' . self::key($name, $labels);

        if (self::hasApcu()) {
            apcu_store($key, $value);
            return;
        }
        self::withFileLock(function (array &$data) use ($key, $value) {
            $data[$key] = $value;
        });
    }

    // ────────────────────────────────────────────────────────── HISTOGRAM ───

    /**
     * Observe a value into a histogram. Internally this performs several
     * atomic increments: per-bucket counts, +Inf bucket, _count and _sum.
     *
     * Note: sum is stored in integer micro-units (value * 1e6) to allow
     * atomic integer increments in APCu.
     */
    public static function observe(
        string $name,
        float $value,
        array $buckets,
        array $labels = []
    ): void {
        $baseKey = self::key($name, $labels);

        if (self::hasApcu()) {
            // Conta in tutti i bucket il cui upper bound >= value
            foreach ($buckets as $b) {
                if ($value <= $b) {
                    apcu_inc('h_b:' . $baseKey . ':' . $b, 1, $success, 0);
                }
            }
            // Bucket +Inf raccoglie sempre
            apcu_inc('h_b:' . $baseKey . ':+Inf', 1, $success, 0);
            // Sum (in micro-unit per restare integer)
            apcu_inc('h_s:' . $baseKey, (int)round($value * 1_000_000), $success, 0);
            // Count
            apcu_inc('h_c:' . $baseKey, 1, $success, 0);
            return;
        }

        // Fallback filesystem: una sola write sotto lock per N operazioni
        self::withFileLock(function (array &$data) use ($baseKey, $value, $buckets) {
            foreach ($buckets as $b) {
                if ($value <= $b) {
                    $k = 'h_b:' . $baseKey . ':' . $b;
                    $data[$k] = ($data[$k] ?? 0) + 1;
                }
            }
            $kinf = 'h_b:' . $baseKey . ':+Inf';
            $data[$kinf] = ($data[$kinf] ?? 0) + 1;
            $ks = 'h_s:' . $baseKey;
            $data[$ks] = ($data[$ks] ?? 0) + (int)round($value * 1_000_000);
            $kc = 'h_c:' . $baseKey;
            $data[$kc] = ($data[$kc] ?? 0) + 1;
        });
    }

    // ────────────────────────────────────────────────────────────── DUMP ────

    /**
     * Return all metrics as a flat array keyed by internal keys.
     * Example: ['c:nexus_foo{a=b}' => 42, 'g:nexus_bar' => 3.14, ...]
     * Used by /api/metrics.php to render Prometheus text format.
     */
    public static function dump(): array
    {
        if (self::hasApcu()) {
            $out = [];
            // apcu_cache_info() can be expensive; prefer APCUIterator
            // iteration when available.
            if (class_exists('APCUIterator')) {
                $it = new APCUIterator('/^(c:|g:|h_b:|h_s:|h_c:)/');
                foreach ($it as $entry) {
                    $out[$entry['key']] = $entry['value'];
                }
                return $out;
            }
            // Fallback: scan APCu cache entries (slower)
            $info = apcu_cache_info();
            foreach ($info['cache_list'] ?? [] as $entry) {
                $k = $entry['info'] ?? $entry['key'] ?? null;
                if ($k === null) continue;
                if (!preg_match('/^(c:|g:|h_b:|h_s:|h_c:)/', $k)) continue;
                $v = apcu_fetch($k);
                if ($v !== false) $out[$k] = $v;
            }
            return $out;
        }
        // Filesystem: leggi il file una volta
        if (!file_exists(NEXUS_METRICS_FILE)) {
            return [];
        }
        $raw = @file_get_contents(NEXUS_METRICS_FILE);
        if ($raw === false || $raw === '') return [];
        $data = json_decode($raw, true);
        return is_array($data) ? $data : [];
    }

    /**
     * Reset all metrics. Useful for testing only. Do NOT call in
     * production: resetting counters breaks Prometheus `rate()` semantics.
     */
    public static function reset(): void
    {
        if (self::hasApcu()) {
            apcu_clear_cache();
            return;
        }
        @unlink(NEXUS_METRICS_FILE);
    }

    // ─────────────────────────────────────────────────────── INTERNALS ─────

    /**
     * Perform a read-modify-write on the JSON storage file under an
     * exclusive flock. The callback receives the data array by reference.
     */
    private static function withFileLock(callable $callback): void
    {
        // Open file with 'c+' mode: create if missing, position at 0.
        $fp = @fopen(NEXUS_METRICS_FILE, 'c+');
        if ($fp === false) {
            // Do not throw to the client: the metric will be lost but the
            // request should continue. Log a server-side error.
            error_log('[nexus-metrics] cannot open metrics file: ' . NEXUS_METRICS_FILE);
            return;
        }
        try {
            if (!flock($fp, LOCK_EX)) {
                return;
            }
            $raw = stream_get_contents($fp);
            $data = ($raw === '') ? [] : (json_decode($raw, true) ?: []);
            $callback($data);
            ftruncate($fp, 0);
            rewind($fp);
            fwrite($fp, json_encode($data, JSON_UNESCAPED_SLASHES));
            fflush($fp);
            flock($fp, LOCK_UN);
        } finally {
            fclose($fp);
        }
    }
}
