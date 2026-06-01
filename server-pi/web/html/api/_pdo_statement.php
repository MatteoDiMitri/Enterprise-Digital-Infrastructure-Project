<?php
/**
 * api/_pdo_statement.php
 *
 * PDOStatement wrapper that intercepts `execute()` to measure query
 * latency and record database metrics.
 *
 * How it works:
 * - PHP's `PDO::ATTR_STATEMENT_CLASS` allows returning instances of a
 *   custom class for prepared statements. By setting that attribute
 *   the application code can continue to call prepare()/execute() and
 *   our wrapper will record timings transparently.
 *
 * Caveats:
 * - Queries executed via `PDO::exec()` or `PDO::query()` (without
 *   prepare) are not measured by this class.
 * - The PDOStatement constructor is private; we override `execute()`
 *   to measure and propagate results/errors.
 * - The custom class must extend PDOStatement for PDO to accept it.
 */

declare(strict_types=1);

require_once __DIR__ . '/_metrics_store.php';

final class NexusPDOStatement extends PDOStatement
{
    /**
     * PDO instantiates this internally. Constructor must be protected
     * (not public) or PHP raises an exception.
     */
    protected function __construct() { /* PDO provides no constructor args */ }

    /**
     * Override execute(): time the query, record metrics, and rethrow
     * errors while preserving the PDO contract.
     *
     * @param array|null $params bind parameters (same as PDOStatement)
     * @return bool              true on success
     */
    public function execute(?array $params = null): bool
    {
        $t0 = microtime(true);
        $type = $this->detectQueryType();
        $error = false;

        try {
            $ok = parent::execute($params);
            if (!$ok) {
                $error = true;
                // Non lanciamo eccezione: lasciamo che PDO faccia
                // quello che è configurato a fare (ERRMODE_EXCEPTION
                // tirerà comunque su nel caller).
            }
            return $ok;
        } catch (\Throwable $e) {
            $error = true;
            throw $e;          // re-throw: non rompiamo il contratto PDO
        } finally {
            $elapsed = microtime(true) - $t0;
            $labels = [
                'type'   => $type,
                'status' => $error ? 'error' : 'ok',
            ];
            MetricsStore::inc('nexus_db_queries_total', $labels);
            MetricsStore::observe(
                'nexus_db_query_duration_seconds',
                $elapsed,
                NEXUS_DB_BUCKETS,
                ['type' => $type]
            );
        }
    }

    /**
     * Classify the query into types we care about for metrics:
     * select / insert / update / delete / transaction / other.
     * `$this->queryString` is populated by PDO at prepare time.
     */
    private function detectQueryType(): string
    {
        $sql = ltrim($this->queryString);
        if ($sql === '') return 'other';

        // Match la prima parola, case insensitive.
        // Niente regex pesanti — siamo nell'hot path.
        $first = strtolower(strtok($sql, " \t\n\r"));
        switch ($first) {
            case 'select':  return 'select';
            case 'insert':  return 'insert';
            case 'update':  return 'update';
            case 'delete':  return 'delete';
            case 'begin':
            case 'start':
            case 'commit':
            case 'rollback': return 'transaction';
            default:        return 'other';
        }
    }
}
