<?php
/**
 * TARGET ENDPOINT FOR LOCUST LOAD GENERATOR
 * * What this code does:
 * This script queries the MariaDB database to fetch the current active product 
 * catalog (specifically just the 'id' and 'price' columns) and outputs it as 
 * a lightweight JSON response.
 * * Why it is used for Locust:
 * Hardcoding product IDs and prices inside the Python load testing scripts is 
 * an anti-pattern. If you update the database, your Locust tests would immediately 
 * break or send requests for "ghost" products (resulting in 404s). 
 * * By exposing this endpoint, Locust can behave like a real client: it queries 
 * this URL once at the start of a test to "learn" what is currently in the store, 
 * saves it in RAM, and then uses that real, dynamic data to populate the simulated 
 * shopping carts sent to the checkout endpoint.
 */

// /var/www/html/api/products.php

// 1. Include the database connection (adjust the path depending on where db.php is)
include '../db.php'; 

// Set the header to return JSON
header('Content-Type: application/json');

try {
    // Locust only needs ID and Price to simulate a cart checkout, 
    // so we optimize the query to NOT fetch descriptions, images, or categories.
    $stmt = $pdo->query("SELECT id, price FROM products");
    $dbProducts = $stmt->fetchAll(PDO::FETCH_ASSOC);

    $apiProducts = [];
    foreach ($dbProducts as $row) {
        $apiProducts[] = [
            'id'    => (int)$row['id'],
            'price' => (float)$row['price']
        ];
    }

    echo json_encode($apiProducts);

} catch (Exception $e) {
    http_response_code(500);
    echo json_encode(['error' => 'Database error']);
}