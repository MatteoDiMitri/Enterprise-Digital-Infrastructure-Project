<?php
/**
 * api/scenario.php
 *
 * Active Locust scenario state endpoint.
 *
 * Usage:
 *   GET  /api/scenario  -> returns the active scenario (or "idle")
 *   POST /api/scenario  -> set the active scenario
 *     body: {"scenario":"flash_crowd","started_at":"ISO-8601"}
 *
 * Flow:
 * - The launcher (FastAPI) POSTs the scenario before returning
 *   after starting a run. When the run ends it POSTs {"scenario":"idle"}.
 * - dashboard_metrics.php reads this file and the UI shows the banner.
 *
 * Storage: a single JSON file at /tmp/nexus_active_scenario.json.
 * Concurrency: writes are rare (start/stop) so simple last-write-wins
 * semantics are used.
 *
 * Security: there is no auth by default. For production restrict POSTs
 * by source IP or require a token in a header.
 */

declare(strict_types=1);

const SCENARIO_FILE = '/tmp/nexus_active_scenario.json';

// Set di scenari validi — duplica la whitelist di launcher/main.py
const ALLOWED_SCENARIOS = [
    'idle',
    'normal',
    'flash_crowd',
    'ddos',
    'checkout_storm',
    'degradation',
    'saturation',
];

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

if ($method === 'OPTIONS') {
    http_response_code(204);
    exit;
}

if ($method === 'GET') {
    if (file_exists(SCENARIO_FILE)) {
        $raw = @file_get_contents(SCENARIO_FILE);
        $data = $raw ? json_decode($raw, true) : null;
        if (is_array($data)) {
            echo json_encode($data);
            exit;
        }
    }
    echo json_encode(['scenario' => 'idle', 'started_at' => null]);
    exit;
}

if ($method === 'POST') {
    $input = file_get_contents('php://input') ?: '{}';
    $data  = json_decode($input, true) ?: [];

    $scenario = $data['scenario'] ?? 'idle';
    if (!in_array($scenario, ALLOWED_SCENARIOS, true)) {
        http_response_code(400);
        echo json_encode(['error' => 'unknown scenario', 'allowed' => ALLOWED_SCENARIOS]);
        exit;
    }

    $payload = [
        'scenario'   => $scenario,
        'started_at' => $data['started_at'] ?? date('c'),
        'params'     => $data['params'] ?? null,    // opzionale: {users, spawn_rate, duration}
    ];

    if ($scenario === 'idle') {
        // When returning to idle remove the file instead of writing
        // scenario=idle. This makes "is there an active run?" cleaner.
        @unlink(SCENARIO_FILE);
        echo json_encode(['scenario' => 'idle', 'cleared' => true]);
        exit;
    }

    if (@file_put_contents(SCENARIO_FILE, json_encode($payload)) === false) {
        http_response_code(500);
        echo json_encode(['error' => 'cannot write scenario file']);
        exit;
    }

    echo json_encode($payload);
    exit;
}

http_response_code(405);
echo json_encode(['error' => 'method not allowed']);
