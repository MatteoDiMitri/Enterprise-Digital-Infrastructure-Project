<?php
/**
 * api/metrics.php
 * ===============
 * Endpoint di esposizione metriche in formato Prometheus text-based
 * (versione 0.0.4 della spec ufficiale).
 *
 * URL
 * ---
 *   GET http://<raspberry>/api/metrics
 *
 * Prometheus lo chiama via `scrape_configs` ogni 5 secondi (default
 * configurato in prometheus.yml).
 *
 * RESPONSE FORMAT
 * ---------------
 * Content-Type: text/plain; version=0.0.4; charset=utf-8
 *
 *   # HELP nexus_http_requests_total Number of HTTP requests received.
 *   # TYPE nexus_http_requests_total counter
 *   nexus_http_requests_total{endpoint="/",method="GET",status="200"} 5420
 *   nexus_http_requests_total{endpoint="/index.php",method="GET",status="200"} 2710
 *   ...
 *   # HELP nexus_http_request_duration_seconds Latency histogram.
 *   # TYPE nexus_http_request_duration_seconds histogram
 *   nexus_http_request_duration_seconds_bucket{endpoint="/",le="0.005"} 0
 *   nexus_http_request_duration_seconds_bucket{endpoint="/",le="0.01"} 50
 *   ...
 *   nexus_http_request_duration_seconds_bucket{endpoint="/",le="+Inf"} 5420
 *   nexus_http_request_duration_seconds_sum{endpoint="/"} 142.5
 *   nexus_http_request_duration_seconds_count{endpoint="/"} 5420
/*
 * NOTE — missing histogram buckets
 * -------------------------------
 * Prometheus requires all declared histogram buckets to be exposed,
 * even those with a count of 0. The internal store omits zero-count
 * buckets to save writes; this endpoint fills missing buckets before
 * emitting the text exposition.
 */

declare(strict_types=1);

require_once __DIR__ . '/_metrics_store.php';

// ────────────────────────────────────────────────────────────────────────────
// HEADERS
// ────────────────────────────────────────────────────────────────────────────
header('Content-Type: text/plain; version=0.0.4; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate');

// ────────────────────────────────────────────────────────────────────────────
// REGISTRO DELLE METRICHE NOTE
// Ogni metrica ha tipo (counter|gauge|histogram), help string, e per gli
// histogram l'elenco completo dei bucket attesi.
// ────────────────────────────────────────────────────────────────────────────
$REGISTRY = [
    // HTTP layer
    'nexus_http_requests_total' => [
        'type' => 'counter',
        'help' => 'Total number of HTTP requests processed by PHP, labeled by endpoint, method and HTTP status code.',
    ],
    'nexus_http_errors_total' => [
        'type' => 'counter',
        'help' => 'Total number of HTTP responses with status >= 400, labeled by error class (4xx/5xx), status code and endpoint.',
    ],
    'nexus_http_request_duration_seconds' => [
        'type'    => 'histogram',
        'help'    => 'HTTP request latency in seconds, observed at the PHP shutdown handler (excludes web-server queue time).',
        'buckets' => NEXUS_HTTP_BUCKETS,
    ],
    'nexus_active_requests_inflight' => [
        'type' => 'counter',  // tecnicamente un "gauge approssimato" via delta-counter
        'help' => 'Net counter of currently in-flight PHP requests (incremented on start, decremented on shutdown).',
    ],

    // Database layer
    'nexus_db_queries_total' => [
        'type' => 'counter',
        'help' => 'Total number of database queries executed via PDO::prepare()->execute(), labeled by query type and ok/error status.',
    ],
    'nexus_db_query_duration_seconds' => [
        'type'    => 'histogram',
        'help'    => 'Latency of individual database queries in seconds, labeled by query type.',
        'buckets' => NEXUS_DB_BUCKETS,
    ],

    // Business layer
    'nexus_checkout_orders_total' => [
        'type' => 'counter',
        'help' => 'Total number of orders processed by /checkout.php, labeled by outcome (success/failure).',
    ],
];

// ────────────────────────────────────────────────────────────────────────────
// LETTURA DALLO STORE
// ────────────────────────────────────────────────────────────────────────────
$raw = MetricsStore::dump();

// Raggruppiamo i dati per metric name. Per ogni metric raccogliamo:
//   - counter:    label_string → value
//   - gauge:      label_string → value
//   - histogram:  label_string → {buckets: [le → cum], sum, count}
$grouped = [];
foreach ($raw as $key => $value) {
    // Le chiavi sono nella forma:
    //   c:metric{labels...}       → counter
    //   g:metric{labels...}       → gauge
    //   h_b:metric{labels...}:le  → histogram bucket
    //   h_s:metric{labels...}     → histogram sum (micro-units)
    //   h_c:metric{labels...}     → histogram count
    if (preg_match('/^c:([a-zA-Z_][a-zA-Z0-9_]*)(\{.*\})?$/', $key, $m)) {
        $name = $m[1]; $labels = $m[2] ?? '';
        $grouped[$name]['type']             = 'counter';
        $grouped[$name]['data'][$labels]    = (int)$value;
    } elseif (preg_match('/^g:([a-zA-Z_][a-zA-Z0-9_]*)(\{.*\})?$/', $key, $m)) {
        $name = $m[1]; $labels = $m[2] ?? '';
        $grouped[$name]['type']             = 'gauge';
        $grouped[$name]['data'][$labels]    = (float)$value;
    } elseif (preg_match('/^h_b:([a-zA-Z_][a-zA-Z0-9_]*)(\{.*\})?:(.+)$/', $key, $m)) {
        $name = $m[1]; $labels = $m[2] ?? ''; $le = $m[3];
        $grouped[$name]['type']                            = 'histogram';
        $grouped[$name]['data'][$labels]['buckets'][$le]   = (int)$value;
    } elseif (preg_match('/^h_s:([a-zA-Z_][a-zA-Z0-9_]*)(\{.*\})?$/', $key, $m)) {
        $name = $m[1]; $labels = $m[2] ?? '';
        $grouped[$name]['type']                  = 'histogram';
        $grouped[$name]['data'][$labels]['sum']  = (int)$value;  // micro-units
    } elseif (preg_match('/^h_c:([a-zA-Z_][a-zA-Z0-9_]*)(\{.*\})?$/', $key, $m)) {
        $name = $m[1]; $labels = $m[2] ?? '';
        $grouped[$name]['type']                    = 'histogram';
        $grouped[$name]['data'][$labels]['count']  = (int)$value;
    }
}

// ────────────────────────────────────────────────────────────────────────────
// EMISSIONE DEL FORMATO PROMETHEUS
// ────────────────────────────────────────────────────────────────────────────
$out = [];

foreach ($REGISTRY as $name => $meta) {
    $type = $meta['type'];
    $help = $meta['help'];

    $out[] = "# HELP {$name} {$help}";
    $out[] = "# TYPE {$name} {$type}";

    $series = $grouped[$name]['data'] ?? [];

    if ($type === 'counter' || $type === 'gauge') {
        if (empty($series)) {
            // Nessun campione raccolto ancora. Per i counter Prometheus
            // accetta "metrica vuota" senza errori. Niente da emettere.
            $out[] = '';
            continue;
        }
        foreach ($series as $labels => $value) {
            $out[] = "{$name}" . self_prom_quote_labels($labels) . " {$value}";
        }
    } elseif ($type === 'histogram') {
        // Per ogni set di label, emetti l'intera struttura histogram.
        if (empty($series)) {
            $out[] = '';
            continue;
        }
        $buckets = $meta['buckets'];
        foreach ($series as $labels => $obs) {
            $bucketData = $obs['buckets'] ?? [];
            $cumulative = 0;
            // Bucket "normali", ordinati per upper bound
            foreach ($buckets as $le) {
                $cumulative = $bucketData[(string)$le] ?? $cumulative;
                $leLabel    = self_prom_format_le($le);
                $out[]      = "{$name}_bucket" . self_prom_with_label($labels, 'le', $leLabel) . " {$cumulative}";
            }
            // Bucket +Inf (sempre uguale al count totale)
            $infCount = $bucketData['+Inf'] ?? ($obs['count'] ?? $cumulative);
            $out[]    = "{$name}_bucket" . self_prom_with_label($labels, 'le', '+Inf') . " {$infCount}";
            // Sum: in micro-units di counting, riconvertito a secondi
            $sumMicro = $obs['sum']   ?? 0;
            $count    = $obs['count'] ?? 0;
            $sumSec   = $sumMicro / 1_000_000;
            $quotedLabels = self_prom_quote_labels($labels);
            $out[]    = "{$name}_sum{$quotedLabels} {$sumSec}";
            $out[]    = "{$name}_count{$quotedLabels} {$count}";
        }
    }
    $out[] = '';
}

echo implode("\n", $out);

// ────────────────────────────────────────────────────────────────────────────
// HELPERS
// ────────────────────────────────────────────────────────────────────────────

/**
 * Formatta un numero di bucket nel formato canonico di Prometheus:
 *   0.005 → "0.005", 0.1 → "0.1", 1.0 → "1", 10 → "10"
 */
function self_prom_format_le($le): string
{
    if (is_string($le)) return $le;
    // PHP stringifica i float in modo strano (1.0 → "1"). Va bene
    // per Prometheus ma assicuriamoci di non perdere precisione.
    if ((float)$le == (int)$le) return (string)(int)$le;
    return (string)$le;
}

/**
 * Converte la rappresentazione interna delle label (non quotate) nel
 * formato Prometheus standard (quotate).
 *
 *   ""                              →  ""
 *   "{a=b,c=d}"                     →  "{a=\"b\",c=\"d\"}"
 *   "{endpoint=/index.php}"         →  "{endpoint=\"/index.php\"}"
 */
function self_prom_quote_labels(string $labels): string
{
    if ($labels === '' || $labels === '{}') {
        return '';
    }
    $inner = trim($labels, '{}');
    if ($inner === '') return '';
    $parts = explode(',', $inner);
    $quoted = [];
    foreach ($parts as $p) {
        [$k, $v] = explode('=', $p, 2) + ['',''];
        $quoted[] = $k . '="' . $v . '"';
    }
    return '{' . implode(',', $quoted) . '}';
}

/**
 * Inserisce un'etichetta aggiuntiva in una stringa di label esistente.
 *   ""                              + le=0.1   → {le="0.1"}
 *   "{a=b,c=d}"                     + le=0.1   → {a="b",c="d",le="0.1"}
 */
function self_prom_with_label(string $existingLabels, string $key, string $value): string
{
    $base = self_prom_quote_labels($existingLabels);
    $newPair = "{$key}=\"{$value}\"";
    if ($base === '') {
        return '{' . $newPair . '}';
    }
    // $base = "{a="b",c="d"}" — togli graffa finale, aggiungi nuova label
    return rtrim($base, '}') . ',' . $newPair . '}';
}
