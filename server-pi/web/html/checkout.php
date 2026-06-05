<?php
/**
 * checkout.php
 *
 * Instrumented checkout endpoint.
 * - Loads the shared metrics store and records order totals by outcome.
 * - Behavior mirrors a normal checkout: server-side total, PDO
 *   transaction (begin/commit/rollback) and JSON response shape.
 *
 * WHAT CHANGED (checkout_storm fix)
 * ---------------------------------
 * The original endpoint only did append-only INSERTs (orders +
 * order_items). Append-only inserts have essentially no lock contention,
 * so under a checkout storm the database was never the bottleneck: DB
 * latency stayed flat and PHP/CPU degraded first. That did NOT match the
 * expected checkout-storm signature (DB latency = the star, MariaDB the
 * first to degrade).
 *
 * Fix: reserve stock under an explicit row lock, exactly like a real
 * checkout would. Inside the transaction we `SELECT ... FOR UPDATE` the
 * involved product rows, in a CONSISTENT order (product id ASC) so
 * concurrent checkouts serialize on the same hot rows instead of
 * deadlocking. With many simultaneous orders hitting the same small
 * catalog, transactions queue on those locks → the instrumented
 * execute() calls wait → DB query latency climbs → MariaDB crosses the
 * 500ms p95 warning threshold first. Browsing (plain SELECT, MVCC
 * non-locking read) is unaffected, so GET / stays healthy — the
 * POST-vs-GET divergence the dashboard is meant to show.
 *
 * Note: this locks (reserves) without mutating stock, so it is safe to
 * run repeatedly. The commented UPDATE shows where a real decrement goes.
 *
 * Other metrics (request latency, DB query counts and durations) are
 * collected automatically by `api/_prepend.php` (per-request) and
 * `api/_pdo_statement.php` (per-query).
 */

require_once __DIR__ . '/api/_metrics_store.php';
include 'db.php';

header('Content-Type: application/json');

$inputJSON = file_get_contents('php://input');
$data      = json_decode($inputJSON, true);

// Empty cart: logical failure
if (!isset($data['items']) || empty($data['items'])) {
    MetricsStore::inc('nexus_checkout_orders_total', ['status' => 'failure', 'reason' => 'empty_cart']);
    echo json_encode(['success' => false, 'error' => 'Cart is empty']);
    exit;
}

try {
    $pdo->beginTransaction();

    // ── INVENTORY RESERVATION UNDER LOCK ────────────────────────────────
    // Lock the distinct product rows in ascending id order. The consistent
    // ordering guarantees no deadlocks (every transaction acquires locks in
    // the same sequence), so concurrent checkouts QUEUE instead of failing.
    // This is what turns the write path DB-bound and lights up DB latency.
    $ids = array_values(array_unique(array_map(
        static fn($item) => (int) $item['id'],
        $data['items']
    )));
    sort($ids, SORT_NUMERIC);

    $lockStmt = $pdo->prepare("SELECT id, stock FROM products WHERE id = ? FOR UPDATE");
    foreach ($ids as $pid) {
        // Each execute() is instrumented and holds an exclusive row lock
        // until commit — this is the contention point under a storm.
        $lockStmt->execute([$pid]);

        // Real systems would decrement here. Left commented so repeated
        // runs don't deplete stock; uncomment for a true reservation:
        // $dec = $pdo->prepare("UPDATE products SET stock = GREATEST(stock - ?, 0) WHERE id = ?");
        // $qty = 0;
        // foreach ($data['items'] as $it) { if ((int)$it['id'] === $pid) { $qty += (int)$it['qty']; } }
        // $dec->execute([$qty, $pid]);
    }

    // Recompute total server-side (do not trust the client)
    $totalPrice = 0;
    $itemsCount = 0;
    foreach ($data['items'] as $item) {
        $totalPrice += $item['price'] * $item['qty'];
        $itemsCount += (int) $item['qty'];
    }

    $stmtOrder = $pdo->prepare("INSERT INTO orders (total_price, items_count) VALUES (?, ?)");
    $stmtOrder->execute([$totalPrice, $itemsCount]);
    $orderId = $pdo->lastInsertId();

    $stmtItem = $pdo->prepare(
        "INSERT INTO order_items (order_id, product_id, quantity, price) VALUES (?, ?, ?, ?)"
    );
    foreach ($data['items'] as $item) {
        $stmtItem->execute([$orderId, $item['id'], $item['qty'], $item['price']]);
    }

    $pdo->commit();

    // Order success
    MetricsStore::inc('nexus_checkout_orders_total', ['status' => 'success']);

    echo json_encode(['success' => true, 'order_id' => $orderId]);

} catch (Exception $e) {
    $pdo->rollBack();

    // Order failure (DB error, deadlock, transaction rollback)
    MetricsStore::inc('nexus_checkout_orders_total', ['status' => 'failure', 'reason' => 'db_error']);

    echo json_encode(['success' => false, 'error' => $e->getMessage()]);
}
