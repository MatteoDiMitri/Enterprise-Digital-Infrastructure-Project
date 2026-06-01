# `scenarios/flash_crowd.py`

Scenario **flash crowd / evento virale**. Simula l'ondata improvvisa di
visitatori che arriva quando un sito finisce sui social, in TV, o sotto
un'offerta lampo.

# Panoramica Architettura

## Come funziona nel suo insieme

A differenza degli altri scenari, qui **la forma del carico nel tempo
è essa stessa lo scenario**. Il numero di utenti non è costante:
parte basso (warm-up), schizza a 2000 in pochi secondi, e poi mantiene
quel livello per circa un minuto prima di terminare.

Questo si ottiene aggiungendo al file una classe `LoadTestShape` che
Locust riconosce automaticamente e usa per pilotare il ramp.

## Flusso dei dati

```
Locust avvia il test
   │
   ▼
FlashCrowdShape.tick()  ← chiamato ogni secondo da Locust
   │
   ├── t < 10s  →  (100, 50)    warm-up: 100 utenti, spawn 50/s
   ├── t < 25s  →  (2000, 200)  burst: ramp a 2000 utenti
   ├── t < 90s  →  (2000, 1)    hold: mantieni 2000 utenti
   └── t ≥ 90s  →  None         fine test
   │
   ▼
Locust sincronizza il pool di utenti al target richiesto
   │
   ▼
Ogni utente esegue il journey di FlashCrowdUser
```

## Componenti del modulo

### `FlashCrowdUser(ShopUser)`

Eredita il journey standard del negozio, ma con due personalizzazioni:

- **`wait_time = between(0.5, 2)`** — gli utenti durante un evento
  virale sono più frettolosi: pochi secondi tra un click e l'altro,
  non i 1-5 s del comportamento "calmo" della baseline.
- **`tasks` con pesi 6/3/1** — il mix di azioni è lo stesso del
  traffico normale: il *carattere* del visitatore non cambia, è solo
  il *volume* a esplodere.

### `FlashCrowdShape(LoadTestShape)`

Pilota il numero di utenti nel tempo tramite il metodo `tick()`. Locust
chiama `tick()` circa ogni secondo: la classe restituisce una tupla
`(utenti_target, spawn_rate)` o `None` per terminare il test.

```python
stages = [
    (10, 100,  50),     # warm-up
    (25, 2000, 200),    # burst
    (90, 2000, 1),      # hold
]
```

Ogni stage è `(tempo_fine_cumulativo, target_utenti, spawn_rate)`. La
forma del ramp è interamente data dal codice — non dai parametri CLI.

## Comportamento importante: `--users` e `--run-time` vengono ignorati

Quando un file Locust contiene una `LoadTestShape`, **i flag `-u`,
`-r`, `-t` della riga di comando NON hanno effetto**. La shape è
l'unica fonte di verità per la forma del carico.

Locust stampa una warning esplicita all'avvio:

```
--run-time, --users or --spawn-rate have no impact on LoadShapes …
```

Questo è atteso e documentato anche nel README di progetto. Il
launcher FastAPI passa comunque i parametri per consistenza, ma per
questo scenario sono ignorati dal motore.

## Pattern usati

- **Strategy temporale.** La forma del carico è separata dal
  comportamento dell'utente: cambiando `FlashCrowdShape.stages` si
  cambia il profilo del ramp senza toccare il journey.
- **State machine implicita.** Il metodo `tick()` consulta il tempo
  trascorso per decidere lo stage corrente. Si potrebbe estendere
  facilmente con curve sinusoidali, gradini multipli, decay
  esponenziale, ecc.

## Estensione

Per modellare un evento diverso (esempio: ramp più ripido, doppia
ondata, decay):

```python
stages = [
    (5,  200,  100),    # warm-up rapido
    (10, 5000, 1000),   # mega-burst
    (60, 5000, 1),      # hold
    (90, 500,  10),     # decay
]
```

Locust accetta qualsiasi sequenza monotona o non monotona di target.

## Dipendenze

```
locust       (LoadTestShape, between)
_base.py     (ShopUser)
```
