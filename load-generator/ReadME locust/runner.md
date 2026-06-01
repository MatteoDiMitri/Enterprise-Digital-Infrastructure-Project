# `launcher/runner.py`

Gestore del **sottoprocesso Locust**. Incapsula tutta la complessità di
lanciare, monitorare e terminare un processo `locust --headless`,
catturando i suoi log in tempo reale.

# Panoramica Architettura

## Come funziona nel suo insieme

`runner.py` è il **cuore tecnico del progetto**: la sola parte di
codice che dialoga con il sistema operativo. Tutto il resto (FastAPI,
HTML) si limita a parlare con il `LocustRunner` esposto qui.

Le responsabilità sono cinque:

1. **Spawning.** Lanciare Locust come processo figlio con i parametri
   giusti.
2. **Capture.** Leggere lo stdout di Locust in tempo reale senza
   bloccare il thread principale.
3. **Buffering.** Tenere a memoria gli ultimi N log per la dashboard.
4. **Classification.** Decorare ogni riga con un livello
   (info/warn/error/ok) per la colorazione UI.
5. **Termination.** Fermare Locust in modo pulito su richiesta.

## Flusso dei dati

```
                                      [Thread main FastAPI]
                                              │
                       start()  ◄──────────── │
                          │                   │
                          ▼                   │
                  subprocess.Popen(           │
                    [python, -m, locust, …],  │
                    stdout=PIPE,              │
                    stderr=STDOUT             │
                  )                           │
                          │                   │
                          ▼                   │
                  thread daemon ────►  pipe ─►│ reader_loop()
                                              │   ↳ ogni riga:
                                              │     ↳ classify()  → info/warn/error/ok
                                              │     ↳ append a deque (maxlen=1000)
                                              │
                                              │
                       status() ──────────────│  copia snapshot del deque
                       stop()    ──────────────│  SIGTERM (+ SIGKILL fallback)
```

## Componenti del modulo

### Costanti di configurazione

```python
MAX_LOG_LINES = 1000           # capacità del ring buffer
STOP_GRACE_SECONDS = 5         # tempo prima del SIGKILL
```

### Classificazione dei log — `_classify()`

Locust non emette log strutturati: stampa stringhe libere su stdout.
Per colorare le righe nella UI senza scrivere parser dipendenti dalla
versione, usiamo tre regex deliberatamente semplici:

```python
LEVEL_PATTERNS = (
    ("error", r"\b(ERROR|CRITICAL|Traceback|Exception|failed)\b"),
    ("warn",  r"\bWARN(ING)?\b"),
    ("ok",    r"\b(All users spawned|Test run complete|Shutting down)\b"),
)
```

La prima regex che matcha vince; tutto il resto è `info`. È un
classificatore *opinionato e conservativo*: preferisce sbagliare
verso `info` piuttosto che gridare falsi positivi.

### La classe `LocustRunner`

#### Stato interno

```python
self._lock = threading.RLock()        # ← reentrant!
self._proc: Optional[subprocess.Popen]
self._reader_thread: Optional[Thread]
self._logs: deque(maxlen=1000)
self._scenario: Optional[str]
self._params: dict
self._started_at / self._stopped_at
```

⚠️ **Perché `RLock` e non `Lock`?** Durante lo sviluppo abbiamo
trovato un deadlock reale: `start()` acquisiva il lock e poi chiamava
`_append_log()`, che ritentava di acquisire lo stesso lock. Con un
`Lock` standard (non rientrante) il thread si bloccava su sé stesso.
`RLock` permette al thread proprietario di riacquisire il lock
ripetutamente: il problema sparisce.

#### `start(scenario, users, spawn_rate, duration, host)`

```python
cmd = [sys.executable, "-m", "locust",
       "-f", str(scenario_path),
       "--headless",
       "-u", str(users),
       "-r", str(spawn_rate),
       "-t", f"{duration}s",
       "--host", target_host]
```

Tre scelte di design importanti:

1. **`sys.executable -m locust`** invece di `locust` puro. Garantisce
   che il Locust eseguito sia quello del venv corrente, non un
   eseguibile sul `PATH` che potrebbe puntare a un'altra
   installazione.
2. **`stderr=subprocess.STDOUT`.** Locust mischia messaggi
   informativi e di errore fra i due stream. Unificarli garantisce
   che il reader thread non perda nulla.
3. **`PYTHONUNBUFFERED=1`** nell'env. Senza questo, Python di Locust
   bufferizza lo stdout: la dashboard riceverebbe i log a blocchi
   grossi con secondi di ritardo.

In caso di errori: se è già in esecuzione → `RuntimeError`; se il file
scenario non esiste → `FileNotFoundError`. Entrambe vengono mappate
ai giusti HTTP code in `main.py`.

#### `stop()`

Sequenza di terminazione:

```
SIGTERM  →  attendi 5 s  →  esce pulito?  → fine
                       └─►  non esce?     → SIGKILL → attendi 2 s
```

`SIGTERM` permette a Locust di chiudere pulitamente le connessioni
e di scrivere il report finale. `SIGKILL` è un last resort se Locust
fosse appeso (è raro).

#### `status()`

Snapshot **non bloccante** per la chiamata HTTP `/status`. Restituisce
una copia del deque (`list(self._logs)`) — mai una reference live —
per evitare che il chiamante veda mutazioni concorrenti.

#### `_reader_loop()` (thread daemon)

Il loop di lettura dello stdout. Punti chiave:

- **Daemon thread**: muore con il processo principale, non blocca lo
  shutdown del launcher.
- **`for raw in proc.stdout`**: itera riga per riga, blocking. È
  sicuro perché vive in un thread separato.
- **`finally` con `proc.wait()`**: quando il pipe si chiude (cioè
  Locust è terminato), il loop salva il `returncode` e marca il run
  come concluso. Questo permette alla dashboard di rilevare la fine
  del test anche senza che l'utente prema *Stop*.

## Pattern usati

- **Producer/Consumer concurrente.** Reader thread = producer (legge
  pipe e scrive deque); FastAPI request handler = consumer (legge
  deque). Sincronizzazione via singolo `RLock`.
- **Ring buffer.** `collections.deque(maxlen=N)` è O(1) sia in
  `append` che in scarto: ideale per un firehose di log.
- **Facade.** Tutta la complessità (subprocess, thread, IPC, segnali)
  sta dietro un'API a 4 metodi (`start`, `stop`, `status`,
  `clear_logs`).
- **Fail-loud sui contratti.** Doppio start → eccezione tipata
  (`RuntimeError`), non un valore di ritorno ambiguo.
- **Defensive copy.** `status()` ritorna una copia del deque per
  evitare race condition sulla view.

## Decisioni di design notevoli

| Decisione                                | Perché                                                     |
| ---------------------------------------- | ---------------------------------------------------------- |
| Singleton (un processo Locust alla volta) | Un laptop non ne supporta utilmente di più                 |
| `RLock` invece di `Lock`                 | Evita deadlock con `_append_log` ricorsivo                 |
| stderr unificato a stdout                | Locust mischia i due, non perdiamo righe                   |
| `PYTHONUNBUFFERED=1`                     | Log live, non a blocchi                                    |
| `python -m locust` invece di `locust`    | Indipendenza dal `PATH`                                    |
| Ring buffer 1000 righe                   | Memoria costante anche su run lunghi                       |
| Regex classifier vs parser strutturato   | Robustezza contro cambi di formato fra versioni di Locust  |

## Dipendenze

```
subprocess, threading, signal, os, re, shlex, sys, time   (stdlib)
collections.deque                                          (stdlib)
datetime, pathlib                                          (stdlib)
typing                                                     (stdlib)
```

Nessuna libreria esterna. Tutto il file è basato sulla standard
library Python.
