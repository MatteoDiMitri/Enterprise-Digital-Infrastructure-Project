# `scenarios/normal.py`

Scenario di **traffico normale**. È il profilo di riferimento (baseline)
contro cui interpretare tutti gli altri scenari.

# Panoramica Architettura

## Come funziona nel suo insieme

`normal.py` produce traffico **realistico e moderato** verso il negozio
PHP: la maggior parte degli utenti sfoglia il catalogo, alcuni aprono
una scheda prodotto, e una piccola frazione completa un acquisto.

Lo scopo è misurare le condizioni "sane" del sistema (latenza media,
percentili p50/p95/p99, throughput, tasso di errore) prima di
sottoporlo a stress. Qualsiasi degradazione osservata negli scenari più
aggressivi ha senso solo in relazione a questa baseline.

## Flusso dei dati

```
Locust → ShopUser.on_start() → GET /
       → ciclo di task:
           ├── 6/10 → task_browse → GET /index.php
           ├── 3/10 → task_detail → GET /index.php?product_id=N
           └── 1/10 → task_order  → POST /checkout.php
       → wait_time random tra 1 e 5 secondi
```

## Componenti del modulo

### Classe `NormalUser(ShopUser)`

Eredita **interamente** il journey da `ShopUser`:
- `wait_time = between(1, 5)` per i think-time umani;
- nessuna soglia di lentezza (`SLOW_THRESHOLD_MS = None`);
- i tre task del journey condiviso.

L'unica cosa che il file dichiara esplicitamente è il dizionario
`tasks`:

```python
tasks = {
    ShopUser.task_browse: 6,
    ShopUser.task_detail: 3,
    ShopUser.task_order:  1,
}
```

I pesi 6/3/1 codificano la regola: **gli utenti sfogliano, solo
alcuni esplorano in dettaglio, ancora meno acquistano**. È il
comportamento canonico di un negozio reale.

## Perché i pesi sono dichiarati qui e non nella base

Locust fonde automaticamente i dizionari `tasks` ereditati. Tenere la
dichiarazione dei pesi nei file concreti (e non in `_base.py`) evita
che le sottoclassi raddoppino accidentalmente i pesi. Vedi
`scenarios/_base.py` per i dettagli.

## Pattern usati

- **Template Method** (riuso della base) + **Strategy** (i pesi sono
  l'unico parametro che cambia).
- **Convention over configuration**: il file è di proposito il più
  corto possibile, perché serve come *modello* per scrivere nuovi
  scenari.

## Esempio di esecuzione CLI

```bash
locust -f scenarios/normal.py --headless \
       -u 100 -r 10 -t 180s \
       --host http://localhost
```

- `-u 100` → 100 utenti concorrenti;
- `-r 10`  → 10 nuovi utenti al secondo;
- `-t 180s` → durata 3 minuti.

## Dipendenze

```
locust       (indirettamente via _base)
_base.py     (ShopUser)
```
