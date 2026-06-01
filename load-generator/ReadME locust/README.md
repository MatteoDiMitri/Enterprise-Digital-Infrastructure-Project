# NEXUS — Piattaforma di Load Testing con Locust

Sistema completo per generare traffico realistico contro un negozio
PHP (`index.php` / `checkout.php`) e per orchestrare i test da una
dashboard HTML locale, senza dover toccare il terminale.

Il progetto è pensato come dimostrazione universitaria: produrre
osservabili (latenza, throughput, error-rate) sotto sei profili di
traffico diversi, dalla folla virale al sovraccarico del database.

---

# Panoramica Architettura

## Come funziona il progetto nel suo insieme

Il sistema è composto da **tre livelli logici** che vivono sulla stessa
macchina (il laptop dello studente / sviluppatore) e da **un target
remoto** che è l'applicazione PHP sotto test.

```
┌─────────────────────────────────────────┐
│  LAPTOP (sviluppatore)                  │
│                                         │
│   ┌──────────────────┐                  │
│   │ control_panel    │  ← browser       │
│   │      .html       │                  │
│   └────────┬─────────┘                  │
│            │ HTTP (fetch)               │
│            ▼                            │
│   ┌──────────────────┐                  │
│   │ FastAPI launcher │  ← localhost:8000│
│   │   (main.py +     │                  │
│   │    runner.py)    │                  │
│   └────────┬─────────┘                  │
│            │ subprocess                 │
│            ▼                            │
│   ┌──────────────────┐                  │
│   │ locust headless  │                  │
│   │  + scenarios/    │                  │
│   └────────┬─────────┘                  │
└────────────┼────────────────────────────┘
             │ HTTP (carico reale)
             ▼
   ┌──────────────────┐
   │  SERVER PHP      │
   │  (XAMPP / MAMP / │
   │   server remoto) │
   └──────────────────┘
```

Ogni livello ha **una sola responsabilità**, e parla con i vicini
attraverso un'interfaccia esplicita. Si possono rimpiazzare le parti
una alla volta (per esempio: sostituire la dashboard con una CLI, o
puntare a un target diverso) senza riscrivere il resto.

## Flusso dei dati: dal click alla metrica

1. **L'utente apre `control_panel.html`** nel browser. La pagina è
   statica (HTML + CSS + JS, nessun bundler), può vivere su `file://`
   o essere servita da qualsiasi web server.

2. **Selezione di uno scenario.** Cliccare una card della "Scenario
   library" pre-compila i parametri (utenti, spawn rate, durata) con
   default sensati per quel profilo di traffico.

3. **Avvio del test.** Il click su *Start test* invia
   `POST /start-test` al launcher FastAPI con un payload JSON:
   ```json
   {
     "scenario": "flash_crowd",
     "users": 2000,
     "spawn_rate": 200,
     "duration": 120,
     "host": "http://localhost"
   }
   ```

4. **Il launcher lancia Locust come processo figlio.** `LocustRunner`
   esegue `python -m locust -f scenarios/<scenario>.py --headless …`
   in un `subprocess`. Stdout e stderr di Locust vengono unificati e
   letti da un thread daemon, che li bufferizza in un `deque`
   circolare da 1000 righe.

5. **Locust genera traffico HTTP** verso il target PHP simulando
   utenti virtuali (`HttpUser`) con think time, navigazione realistica
   (home → browse → detail → opzionale checkout) e pesi sui task
   diversificati per scenario.

6. **Polling dello stato.** La dashboard chiama `GET /status` ogni 2
   secondi e riceve `{running, scenario, started_at, logs, …}`. Le
   nuove righe di log vengono accodate nella console testuale in basso,
   con auto-scroll e classificazione per livello (info/warn/error/ok).

7. **Terminazione.** Il test finisce in due modi: (a) Locust termina
   da solo quando scade `--run-time`, oppure (b) l'utente preme *Stop
   test* e il launcher invia `SIGTERM` al processo (con `SIGKILL` di
   sicurezza dopo 5 s). In entrambi i casi `/status` torna a
   `running:false` al ciclo successivo di polling.

## Come comunicano i componenti

| Da → A                       | Protocollo       | Formato       |
| ---------------------------- | ---------------- | ------------- |
| Browser → FastAPI            | HTTP             | JSON          |
| FastAPI → processo Locust    | `subprocess.Popen` | argv + stdout pipe |
| Processo Locust → target PHP | HTTP             | form / JSON   |

**Tutti i contratti sono espliciti**: gli endpoint REST sono tipizzati
con Pydantic (`StartRequest`), i nomi degli scenari sono in una
whitelist (`ALLOWED_SCENARIOS`), il payload del carrello inviato a
`checkout.php` rispetta lo schema che il file PHP originale si aspetta
(`{items: [{id, qty, price}, …]}`).

## Ruolo delle principali parti

### `scenarios/`
La libreria di *profili di traffico*. Ogni file è un piccolo modulo
Locust che descrive **come** si comporta un utente virtuale. La logica
condivisa (catalogo prodotti, journey di navigazione) sta nel modulo
base `_base.py`; ogni scenario concreto la specializza con pesi
diversi sui task o con un `LoadTestShape` per modellare il ramp.

### `launcher/`
Il *backend di orchestrazione*. Espone tre endpoint HTTP minimi
(`/start-test`, `/stop-test`, `/status`) e nasconde tutta la
complessità di gestire un sottoprocesso (lancio, cattura output,
terminazione pulita, snapshot dello stato). È volutamente piccolo:
non ha database, non ha persistenza, non ha autenticazione — perché
deve girare solo in `127.0.0.1` su una macchina di sviluppo.

### `control_panel.html` + `dashboard.html`
Due pagine web autonome che condividono lo stesso design system
(stesse variabili CSS, stesso topbar, stessi componenti). Il
*Control Panel* lancia i test; il *Dashboard* mostra metriche del
sistema sotto test (KPI, grafici, log di servizio). Sono accoppiati
solo dal link nella topbar e dal fatto che entrambi parlano con la
stessa API.

## Pattern utilizzati

- **Layered architecture (a 3 livelli).** Presentazione (HTML),
  orchestrazione (FastAPI), esecuzione (Locust). Ogni livello dipende
  solo da quello sottostante, mai viceversa.

- **Template Method pattern (negli scenari).** `ShopUser` definisce
  lo *scheletro* del journey (`task_browse`, `task_detail`,
  `task_order`); le sottoclassi non riscrivono i passi, ridefiniscono
  solo `wait_time` e il dizionario `tasks` (cioè i pesi). Aggiungere
  uno scenario nuovo richiede ~10 righe di codice.

- **Singleton runner.** `LocustRunner` è istanziato una volta a livello
  modulo (`launcher/main.py`). Una macchina di test ne ospita un solo
  processo Locust alla volta: il vincolo è esplicito nel codice
  (`/start-test` ritorna HTTP 409 se già in esecuzione), non implicito.

- **Bounded buffer per i log.** I log di Locust possono essere
  decine di migliaia di righe in pochi minuti. Usiamo un
  `collections.deque(maxlen=1000)` come *ring buffer*: nuove righe
  spingono fuori le più vecchie, l'uso di memoria è costante a lungo
  termine.

- **Producer/Consumer threading.** Un thread daemon legge lo stdout
  del sottoprocesso (*producer*), il thread principale di FastAPI lo
  legge via `/status` (*consumer*). La sincronizzazione è garantita
  da un singolo `RLock` ricorrente.

- **Whitelist invece di lookup dinamico.** I nomi di scenario validi
  sono un `set` esplicito in `main.py`. Nessuna chance che un payload
  malizioso del tipo `scenario: "../../../etc/passwd"` arrivi alla
  riga di comando di Locust.

- **Modularità per estensione.** Aggiungere uno scenario: nuovo file
  in `scenarios/`, una riga in `ALLOWED_SCENARIOS`, una entry nel
  vettore `SCENARIOS` del frontend. Nessuna modifica al runner, agli
  endpoint, o agli altri scenari.

## Struttura del progetto

```
nexus/
├── README.md                            ← questo file
├── control_panel.html                   ← UI per lanciare i test
├── dashboard.html                       ← UI per le metriche del sistema
└── locust/
    ├── README.md                        ← guida di esecuzione
    ├── requirements.txt
    ├── locustfile.py                    ← entry-point CLI di default
    ├── scenarios/
    │   ├── _base.py                     ← classe astratta ShopUser
    │   ├── normal.py
    │   ├── flash_crowd.py
    │   ├── ddos.py
    │   ├── checkout_storm.py
    │   ├── degradation.py
    │   └── saturation.py
    └── launcher/
        ├── __init__.py
        ├── main.py                      ← app FastAPI (3 endpoint)
        └── runner.py                    ← gestione del sottoprocesso
```

## Documentazione per file

In `docs/` ogni file di codice ha un proprio README che spiega scopo,
contratto pubblico, dipendenze e scelte di design.
