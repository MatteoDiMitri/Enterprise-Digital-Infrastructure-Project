<?php
// Includiamo la connessione al DB
include 'db.php'; 

// Diciamo al browser che risponderemo con un file JSON
header('Content-Type: application/json');

// Leggiamo il JSON inviato dal JavaScript (la fetch)
$inputJSON = file_get_contents('php://input');
$data = json_decode($inputJSON, true);

// Controlliamo che ci siano prodotti nel carrello
if (!isset($data['items']) || empty($data['items'])) {
    echo json_encode(['success' => false, 'error' => 'Il carrello è vuoto!']);
    exit;
}

try {
    // Iniziamo la "Transazione": o si salva tutto o non si salva niente
    $pdo->beginTransaction();

    // 1. Calcoliamo il totale ricalcolandolo dal server (mai fidarsi del totale inviato dal front-end!)
    $totalPrice = 0;
    foreach ($data['items'] as $item) {
        $totalPrice += $item['price'] * $item['qty'];
    }

    // 2. Creiamo l'ordine nella tabella 'orders'
    $stmtOrder = $pdo->prepare("INSERT INTO orders (total_price) VALUES (?)");
    $stmtOrder->execute([$totalPrice]);
    
    // Recuperiamo l'ID dell'ordine appena creato
    $orderId = $pdo->lastInsertId();

    // 3. Inseriamo ogni prodotto nella tabella 'order_items'
    $stmtItem = $pdo->prepare("INSERT INTO order_items (order_id, product_id, quantity, price) VALUES (?, ?, ?, ?)");
    
    foreach ($data['items'] as $item) {
        $stmtItem->execute([
            $orderId, 
            $item['id'], 
            $item['qty'], 
            $item['price']
        ]);
    }

    // Se tutto è andato bene, confermiamo la transazione
    $pdo->commit();

    // Rispondiamo al JavaScript con successo e l'ID dell'ordine
    echo json_encode(['success' => true, 'order_id' => $orderId]);

} catch (Exception $e) {
    // Se c'è un errore (es. DB offline), annulliamo tutto
    $pdo->rollBack();
    echo json_encode(['success' => false, 'error' => $e->getMessage()]);
}
?>
