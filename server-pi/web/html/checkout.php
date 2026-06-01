<?php
/**
 * checkout.php
 *
 * Instrumented checkout endpoint.
 * - Loads the shared metrics store and records order totals by outcome.
 * - Behavior mirrors a normal checkout: server-side total, PDO
 *   transaction (begin/commit/rollback) and JSON response shape.
 *
 * Other metrics (request latency, DB query counts and durations) are
 * collected automatically by `api/_prepend.php` (per-request) and
 * `api/_pdo_statement.php` (per-query) when enabled.
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

    // Recompute total server-side (do not trust the client)
    $totalPrice = 0;
    foreach ($data['items'] as $item) {
        $totalPrice += $item['price'] * $item['qty'];
    }

    $stmtOrder = $pdo->prepare("INSERT INTO orders (total_price) VALUES (?)");
    $stmtOrder->execute([$totalPrice]);
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
