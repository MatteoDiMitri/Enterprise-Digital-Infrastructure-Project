<?php
/**
 * db.php
 *
 * PDO database connection with optional query instrumentation.
 * - Loads `NexusPDOStatement` to measure query latency when enabled.
 * - Sets `PDO::ATTR_STATEMENT_CLASS` so prepared statements are wrapped
 *   without changing application code.
 *
 * To disable DB metrics for benchmarking, comment out the `setAttribute`
 * line below. The application behavior remains the same.
 */

require_once __DIR__ . '/api/_pdo_statement.php';

$host = getenv('MYSQL_HOST') ?: 'db';
$db   = getenv('MYSQL_DATABASE') ?: 'edi_project';
$user = getenv('MYSQL_USER') ?: null;
$pass = getenv('MYSQL_PASSWORD') ?: null;
$charset = getenv('MYSQL_CHARSET') ?: 'utf8mb4';

if (empty($user) || empty($pass)) {
    throw new \RuntimeException('Database credentials not configured. Create a .env file with MYSQL_USER and MYSQL_PASSWORD, and ensure the web service loads it.');
}

$dsn = "mysql:host={$host};dbname={$db};charset={$charset}";
$options = [ 
    PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    PDO::ATTR_EMULATE_PREPARES   => false,
];

try {
    $pdo = new PDO($dsn, $user, $pass, $options);

    // ── ISTRUMENTAZIONE ─────────────────────────────────────────────
    // Da questo punto in poi, ogni $pdo->prepare(...) restituisce
    // un NexusPDOStatement invece di un PDOStatement standard.
    // Le metriche DB vengono raccolte automaticamente.
    $pdo->setAttribute(PDO::ATTR_STATEMENT_CLASS, ['NexusPDOStatement']);

} catch (\PDOException $e) {
    throw new \PDOException($e->getMessage(), (int)$e->getCode());
}
