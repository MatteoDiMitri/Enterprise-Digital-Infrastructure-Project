# `scenarios/checkout_storm.py`

Scenario **checkout storm**. Stress mirato sul percorso di scrittura:
molti utenti che cercano di completare un ordine quasi simultaneamente.

# Panoramica Architettura

## Come funziona nel suo insieme

Mentre la maggior parte degli scenari stressa principalmente le
operazioni di lettura (GET sulle pagine prodotto), questo scenario si
concentra sul **percorso write**:

```
client → POST /checkout.php → PHP → PDO → MySQL BEGIN TRANSACTION
                                         → INSERT INTO orders
                                         → INSERT INTO order_items × N
                                         → COMMIT
```

Lo scopo è far emergere problemi che si vedono **solo sotto carico
write concorrente**: lock di tabella, contention sui mutex di PDO,
saturazione del connection pool di MySQL, deadlock fra transazioni,
crescita del *innodb_log_file* sotto burst di INSERT.

## Flusso dei dati

```
Locust → CheckoutStormUser
       → ciclo di task:
           ├── 1/11  → task_browse → GET /index.php  (mimica realistica)
           └── 10/11 → task_order  → POST /checkout.php  ← fuoco principale
       → wait_time random 0.5–2 s
```

Il piccolo peso su `task_browse` è intenzionale: un flood di solo
POST sarebbe sintetico in modo evidente e potrebbe essere filtrato
da WAF o middleware di rate limiting. Mantenere una manciata di GET
mimetizza il carico in modo più simile a un *rush di acquisti*
genuino (per esempio: drop di un prodotto in edizione limitata).

## Componenti del modulo

### Classe `CheckoutStormUser(ShopUser)`

```python
wait_time = between(0.5, 2)
tasks = {
    ShopUser.task_browse: 1,
    ShopUser.task_order:  10,
}
```

- **`wait_time` ridotto** (0.5–2 s) perché in una situazione di rush
  gli utenti non perdono tempo a sfogliare.
- **`tasks`** sbilancia drasticamente verso `task_order`. Il fattore
  10:1 garantisce che la grande maggioranza delle richieste sia POST.
- **`task_browse` non è eliminato** perché lascia un minimo di traffico
  GET nel mix, rendendo il pattern più verosimile.

### Riuso di `task_order` dalla base

`task_order` di `ShopUser` già fa tutto quello che serve:
- genera un carrello con `random_cart()`;
- invia il payload JSON a `/checkout.php`;
- valida sia l'HTTP code che il flag `success` nel JSON di risposta.

`checkout_storm.py` non riscrive nulla di tutto questo: punta semplicemente
allo stesso metodo con un peso più alto. **Aggiungere logica
specifica di checkout qui sarebbe un errore di duplicazione.**

## Cosa osservare durante il test

| Metrica                          | Sintomo di problema                                     |
| -------------------------------- | ------------------------------------------------------- |
| Latenza p99 di `POST /checkout`  | Schiacciamento netto sopra i 2-3 s → lock contention    |
| Tasso di errore                  | Spike di 500 → eccezioni PDO, probabilmente deadlock    |
| `success: false` nel JSON        | Errori applicativi (DB offline, transazione abortita)   |
| CPU del DB vs CPU di PHP         | Sbilanciamento → bottleneck identificato                |

## Pattern usati

- **Riuso dei task** della base via Template Method.
- **Peso non binario.** Il rapporto 10:1 è la sola differenza
  comportamentale rispetto a `normal.py`: cambia il *focus* del test
  senza riscrivere il journey.

## Esempio di esecuzione CLI

```bash
locust -f scenarios/checkout_storm.py --headless \
       -u 500 -r 50 -t 120s \
       --host http://localhost
```

## Dipendenze

```
locust       (between)
_base.py     (ShopUser)
```
