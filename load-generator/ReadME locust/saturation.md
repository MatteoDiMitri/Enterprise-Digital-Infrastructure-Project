# `scenarios/saturation.py`

Scenario di **saturazione del backend**. Spinge il sistema fino al suo
limite di throughput per trovare il punto in cui la latenza esplode o
l'error rate sale oltre l'SLO.

# Panoramica Architettura

## Come funziona nel suo insieme

Diversamente dal DDoS, questo scenario stressa l'**intero stack**
(non solo la rete e il front-end), preservando il journey utente
completo: home → browse → detail → order. La differenza con la
baseline è solo il *ritmo*: think-time pressoché nullo, così ogni
utente virtuale satura il throughput aggregato richiedibile dal pool.

L'obiettivo è una *capacity discovery*: a quanti req/s il sistema
inizia a degradare? Dove sta il bottleneck — CPU del web server,
connessioni MySQL, I/O del disco, sincronizzazione del cache?

## Flusso dei dati

```
Locust → SaturationUser
       → ciclo di task molto stretto:
           ├── 8/13 → task_browse  → GET /index.php
           ├── 4/13 → task_detail  → GET /index.php?product_id=N
           └── 1/13 → task_order   → POST /checkout.php
       → wait_time random 0–50 ms (quasi nullo)
```

## Componenti del modulo

### Classe `SaturationUser(ShopUser)`

```python
wait_time = between(0, 0.05)

tasks = {
    ShopUser.task_browse: 8,
    ShopUser.task_detail: 4,
    ShopUser.task_order:  1,
}
```

- **`wait_time = between(0, 0.05)`** — fino a 50 ms casuali tra una
  request e l'altra. Un floor minimo (anziché zero puro) evita il
  *busy spin* sul client Python, che diventerebbe lui stesso il
  bottleneck del test.
- **`tasks` con pesi 8/4/1** — sbilanciato verso le letture, perché:
  - Per saturare il *write path* esiste già `checkout_storm.py`.
  - Una saturazione bilanciata che include molti POST renderebbe il
    test difficile da interpretare: la latenza dei GET sarebbe
    contaminata dalle transazioni MySQL dei POST.
  - In un negozio reale, anche sotto picco, il rapporto letture:scritture
    è dell'ordine di 10:1 o superiore.

## Cosa misurare

Lo scenario è utile soprattutto per produrre un **grafico tipico
"hockey stick"** della latenza in funzione del throughput:

```
latenza p99
   │
   │                                  ╱
   │                              ╱
   │                          ╱
   │                      ╱
   │ ─ ─ ─ ─ ─ ─ ─ ─ ─ ╱── SLO p99 = 500ms
   │                ╱
   │            ╱
   │       ╱
   │  ╱
   └────────────────────────────────────── req/s
                                ↑
                          punto di saturazione
```

Il *gomito* della curva è il throughput massimo sostenibile, quello
sopra il quale il sistema viola l'SLO.

## Pattern usati

- **Stress controllato.** A differenza del DDoS, qui si stressa il
  *journey completo*, non un endpoint isolato. Il risultato è una
  misura di capacità realistica del sistema.
- **Separazione delle preoccupazioni.** Letture e scritture vengono
  stressate in scenari diversi (questo + `checkout_storm`) per
  permettere diagnosi pulite.

## Esempio di esecuzione CLI

```bash
locust -f scenarios/saturation.py --headless \
       -u 5000 -r 500 -t 120s \
       --host http://localhost
```

⚠️ **Avvertenza pratica.** Su una macchina di sviluppo, oltre i ~1000
utenti virtuali Locust è esso stesso un collo di bottiglia (un
processo Python single-threaded gestisce tutti gli eventi). Per
spingersi oltre serve il *distributed mode* di Locust (master +
worker), fuori scope per questo progetto universitario.

## Dipendenze

```
locust       (between)
_base.py     (ShopUser)
```
