# `scenarios/degradation.py`

Scenario di **degradazione / partial failure**. Genera traffico normale
contro un backend che si **suppone** misbehaving (risposte lente, errori
intermittenti, endpoint instabili), e fa emergere quei problemi nel
report di Locust.

# Panoramica Architettura

## Come funziona nel suo insieme

Questo scenario ha un punto di vista diverso dagli altri: **non
inietta degradazione lato server** (non manda payload malformati, non
attacca il backend). Genera invece un traffico realistico ma con
**criteri di valutazione più severi**, in modo che eventuali sintomi
di degradazione lato server diventino visibili nelle metriche di
Locust.

In pratica: se il backend ha un problema (DB lento, upstream
congestionato, cache fredda), questo scenario lo *amplifica nel
report* anziché nasconderlo dentro la categoria "200 OK".

## Flusso dei dati

```
Locust → DegradationUser
       → ciclo di task:
           ├── task_browse        → GET /index.php
           │                        ↳ se latenza > 1500ms → marca FAILURE
           ├── task_detail        → GET /index.php?product_id=N
           │                        ↳ stessa soglia di lentezza
           ├── task_order         → POST /checkout.php
           │                        ↳ stessa soglia di lentezza
           └── task_broken_link   → GET /this-page-does-not-exist
                                    ↳ atteso 404, verifica osservabilità
```

Quando una risposta supera la soglia, Locust contabilizza l'evento
come *failure* nel report finale anche se l'HTTP code è 200. Senza
questo trattamento, una tail-latency catastrofica sarebbe invisibile
sotto la voce "richieste riuscite".

## Componenti del modulo

### Classe `DegradationUser(ShopUser)`

```python
wait_time = between(1, 5)
SLOW_THRESHOLD_MS = 1500
```

- **`wait_time`** è quello "umano" — il traffico è normale, non
  aggressivo. Lo scenario non vuole stressare il backend, vuole
  *osservarlo*.
- **`SLOW_THRESHOLD_MS = 1500`** attiva il meccanismo di
  `_mark_slow_if_needed` ereditato da `ShopUser`: ogni risposta più
  lenta di 1.5 s viene marcata come *failure* nel report Locust.

### Il task aggiuntivo `task_broken_link`

```python
def task_broken_link(self):
    self.client.get("/this-page-does-not-exist", name="GET /…404")
```

Aggiunge un pattern di richieste 404. Non è un errore del codice del
client: è **rumore intenzionale**, utile per:

- verificare che le dashboard di monitoraggio (Grafana, Prometheus,
  nginx access log) distinguano correttamente 4xx da 5xx;
- assicurarsi che il rate-limiting o il WAF non interpretino male
  questo traffico;
- generare una linea di base per il tasso di errore atteso.

### Dizionario `tasks`

```python
tasks = {
    ShopUser.task_browse:  6,
    ShopUser.task_detail:  3,
    ShopUser.task_order:   1,
    task_broken_link:      1,
}
```

Mix di traffico identico alla baseline + una piccola percentuale di
404. La distribuzione 6/3/1 garantisce confrontabilità con `normal.py`.

## Esecuzione e segnali attesi

```bash
locust -f scenarios/degradation.py --headless \
       -u 300 -r 20 -t 180s \
       --host http://localhost
```

- Se il backend è sano: 0 failure dovute a lentezza, 4xx rate ≈ 9%
  (il peso del task 404 sul totale).
- Se il backend ha problemi: failure rate sale per la soglia di
  lentezza, con un mix di 4xx attesi e 5xx imprevisti.

## Pattern usati

- **Observer (lato client).** Lo scenario osserva la qualità del
  servizio, non la corrompe.
- **Strategy via attributo di classe.** Attivare il *late-failure*
  richiede solo `SLOW_THRESHOLD_MS = 1500`: la logica condivisa è in
  `ShopUser._mark_slow_if_needed`.
- **Test del sistema di osservabilità.** Iniettando 404 deterministici
  si verifica indirettamente la pipeline di logging e alerting.

## Quando l'exit code di Locust è 1

Locust esce con returncode `1` se ci sono stati *failure* nel test —
incluse le risposte marcate come lente. Per questo scenario un exit
code non-zero è **atteso** in presenza di tail latency, non è un
errore del codice del progetto.

## Dipendenze

```
random       (stdlib, non usato direttamente qui ma importato per coerenza)
locust       (between)
_base.py     (ShopUser)
```
