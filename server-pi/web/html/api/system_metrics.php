<?php
/**
 * api/system_metrics.php
 *
 * Expose a small set of system metrics (CPU, memory, load average,
 * uptime) in Prometheus text format by reading `/proc` directly.
 *
 * Why not use node_exporter?
 * - node_exporter is the canonical choice, but it adds an extra
 *   dependency (daemon/service). For this demo we prefer zero-extra
 *   packages and a tiny set of useful metrics.
 *
 * Exposed metrics include:
 * - nexus_system_cpu_percent (gauge 0-100)
 * - nexus_system_memory_total_bytes (gauge)
 * - nexus_system_memory_used_bytes (gauge)
 * - nexus_system_memory_used_percent (gauge 0-100)
 * - nexus_system_load_average{period="1m|5m|15m"} (gauge)
 * - nexus_system_uptime_seconds (counter)
 *
 * Note: CPU% is computed by comparing two /proc/stat samples and
 * caching the previous reading (APCu or file) to compute the delta
 * between scrapes.
 */

declare(strict_types=1);

require_once __DIR__ . '/_metrics_store.php';

header('Content-Type: text/plain; version=0.0.4; charset=utf-8');
header('Cache-Control: no-store');

// ────────────────────────────────────────────────────────────────────────────
// CPU PERCENTAGE
// ────────────────────────────────────────────────────────────────────────────

/**
 * Read /proc/stat and return [busy, total] CPU jiffies where busy is
 * user+nice+system. Values are in kernel jiffies.
 */
function nexus_read_cpu_times(): array
{
    $line = @file_get_contents('/proc/stat');
    if ($line === false) return [0, 0];
    // Prima riga: "cpu  user nice system idle iowait irq softirq steal ..."
    if (!preg_match('/^cpu\s+([0-9 ]+)/m', $line, $m)) return [0, 0];
    $vals  = preg_split('/\s+/', trim($m[1]));
    $user   = (int)($vals[0] ?? 0);
    $nice   = (int)($vals[1] ?? 0);
    $system = (int)($vals[2] ?? 0);
    $idle   = (int)($vals[3] ?? 0);
    $iowait = (int)($vals[4] ?? 0);
    $busy   = $user + $nice + $system;
    $total  = $busy + $idle + $iowait;
    return [$busy, $total];
}

/**
 * Calcola la CPU% confrontando con l'ultima lettura salvata.
 * Al primo scrape ritorna 0 (nessun riferimento storico ancora).
 */
function nexus_cpu_percent(): float
{
    [$busy, $total] = nexus_read_cpu_times();

    // Recupera la lettura precedente
    if (MetricsStore::hasApcu()) {
        $prev = apcu_fetch('nexus_cpu_prev', $hit);
        apcu_store('nexus_cpu_prev', ['busy' => $busy, 'total' => $total]);
    } else {
        $cache = '/tmp/nexus_cpu_prev.json';
        $prev = file_exists($cache) ? json_decode(file_get_contents($cache), true) : null;
        @file_put_contents($cache, json_encode(['busy' => $busy, 'total' => $total]));
    }

    if (!$prev || !isset($prev['busy'])) return 0.0;

    $dBusy  = $busy  - $prev['busy'];
    $dTotal = $total - $prev['total'];
    if ($dTotal <= 0) return 0.0;
    return round(($dBusy / $dTotal) * 100.0, 2);
}

// ────────────────────────────────────────────────────────────────────────────
// MEMORY
// ────────────────────────────────────────────────────────────────────────────

function nexus_memory_info(): array
{
    $raw = @file_get_contents('/proc/meminfo');
    if ($raw === false) return ['total' => 0, 'available' => 0];
    $info = [];
    foreach (explode("\n", $raw) as $line) {
        if (preg_match('/^(\w+):\s+(\d+)\s*kB$/', $line, $m)) {
            $info[$m[1]] = (int)$m[2] * 1024;  // → bytes
        }
    }
    $total     = $info['MemTotal']     ?? 0;
    // MemAvailable è la metrica corretta (tiene conto di buffers/cache
    // reclaimabili). Fallback su MemFree per kernel molto vecchi.
    $available = $info['MemAvailable'] ?? $info['MemFree'] ?? 0;
    return ['total' => $total, 'available' => $available];
}

// ────────────────────────────────────────────────────────────────────────────
// LOAD AVERAGE
// ────────────────────────────────────────────────────────────────────────────

function nexus_load_avg(): array
{
    $raw = @file_get_contents('/proc/loadavg');
    if ($raw === false) return [0, 0, 0];
    $parts = preg_split('/\s+/', trim($raw));
    return [(float)$parts[0], (float)$parts[1], (float)$parts[2]];
}

// ────────────────────────────────────────────────────────────────────────────
// UPTIME
// ────────────────────────────────────────────────────────────────────────────

function nexus_uptime_seconds(): float
{
    $raw = @file_get_contents('/proc/uptime');
    if ($raw === false) return 0.0;
    return (float)strtok($raw, ' ');
}

// ────────────────────────────────────────────────────────────────────────────
// COLLECTION + EXPOSURE
// ────────────────────────────────────────────────────────────────────────────

$cpu       = nexus_cpu_percent();
$mem       = nexus_memory_info();
[$l1, $l5, $l15] = nexus_load_avg();
$uptime    = nexus_uptime_seconds();

$memUsedBytes = $mem['total'] - $mem['available'];
$memUsedPct   = $mem['total'] > 0
    ? round(($memUsedBytes / $mem['total']) * 100.0, 2)
    : 0.0;

$lines = [
    '# HELP nexus_system_cpu_percent CPU utilization percentage across all cores (delta-based, 0-100).',
    '# TYPE nexus_system_cpu_percent gauge',
    "nexus_system_cpu_percent {$cpu}",
    '',
    '# HELP nexus_system_memory_total_bytes Total physical memory in bytes (MemTotal from /proc/meminfo).',
    '# TYPE nexus_system_memory_total_bytes gauge',
    "nexus_system_memory_total_bytes {$mem['total']}",
    '',
    '# HELP nexus_system_memory_used_bytes Used memory in bytes (MemTotal - MemAvailable).',
    '# TYPE nexus_system_memory_used_bytes gauge',
    "nexus_system_memory_used_bytes {$memUsedBytes}",
    '',
    '# HELP nexus_system_memory_used_percent Memory utilization percentage (0-100).',
    '# TYPE nexus_system_memory_used_percent gauge',
    "nexus_system_memory_used_percent {$memUsedPct}",
    '',
    '# HELP nexus_system_load_average System load average for 1, 5 and 15 minutes.',
    '# TYPE nexus_system_load_average gauge',
    "nexus_system_load_average{period=\"1m\"} {$l1}",
    "nexus_system_load_average{period=\"5m\"} {$l5}",
    "nexus_system_load_average{period=\"15m\"} {$l15}",
    '',
    '# HELP nexus_system_uptime_seconds Number of seconds since the system booted.',
    '# TYPE nexus_system_uptime_seconds counter',
    "nexus_system_uptime_seconds {$uptime}",
    '',
];

echo implode("\n", $lines);
