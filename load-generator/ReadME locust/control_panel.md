# `control_panel.html`

Dashboard HTML statica per **lanciare e controllare** i test Locust dal
browser, senza terminale. È il front-end del launcher FastAPI.

# Panoramica Architettura

## Come funziona nel suo insieme

Single-page application puramente lato client, senza build tool, senza
framework. Tre tecnologie:

- **HTML** — markup della pagina;
- **CSS** — design system condiviso con `dashboard.html`;
- **JavaScript vanilla** — interazione e fetch HTTP verso il launcher.

Tutto in un singolo file da ~700 righe. Niente bundler, niente
transpiler, niente node_modules. Aprire il file con un doppio click
funziona.

## Flusso dei dati

```
                                   ┌──────────────────────────┐
                                   │  control_panel.html      │
                                   │                          │
   utente clicca "Start test" ───► │   startTest()            │
                                   │      │                   │
                                   │      ▼ fetch             │
                                   │   POST /start-test ──────┼──► FastAPI launcher
                                   │                          │
   ogni 2 secondi    ◄─────────────┤   setInterval(pollStatus)│
                                   │      │                   │
                                   │      ▼ fetch             │
                                   │   GET /status ───────────┼──► FastAPI launcher
                                   │      │                   │
                                   │      ▼                   │
                                   │   appendLogs(...)        │
                                   │   renderStatus(...)      │
                                   └──────────────────────────┘
```

## Sezioni della pagina

### Topbar
Logo, breadcrumb, navigazione (Shop / Team / Control Panel /
Dashboard), stato del launcher (online/offline), pill di stato
RUNNING/IDLE, orologio. Comuni con `dashboard.html` per coerenza
visiva.

### Header "Mission control"
Banner che riassume lo stato corrente: scenario selezionato,
running/idle, elapsed time del run attivo.

### Scenario library
Griglia di 6 card, una per scenario. Cliccando una card:

1. Si applica la classe `.selected` (bordo rosso, badge "SELECTED");
2. I default dello scenario vengono scritti nei campi di parametri
   sottostanti;
3. Il nome dello scenario appare nell'header.

### Run parameters
Tre input numerici (users / spawn rate / duration) + due bottoni
START / STOP. Validazione lato client per evitare submit con campi
vuoti. I campi si bloccano durante un run per evitare confusione.

### Launcher endpoint + Target host
Due input testuali sotto i parametri:

- **Launcher endpoint** — URL del backend FastAPI (default
  `http://localhost:8000`);
- **Target host** — URL del sito sotto test (default
  `http://localhost`).

Permettono di spostare il test su un'altra macchina senza modificare
il codice.

### Execution log
Console testuale con auto-scroll, classificazione per livello
(info/warn/error/ok), contatore di righe, bottone Clear. Mostra
direttamente lo stdout di Locust come arriva.

## Componenti JavaScript

### `SCENARIOS` (array di config)

Unica fonte di verità per gli scenari disponibili. Ogni entry:

```javascript
{
  key:      "flash_crowd",       // chiave per /start-test
  icon:     "🔥",
  name:     "Flash Crowd",
  tag:      "spike", tagText: "viral spike",
  desc:     "...",
  defaults: { users: 2000, spawn_rate: 200, duration: 120 },
}
```

Le card vengono renderizzate iterando questo array — aggiungere uno
scenario nuovo significa aggiungere una entry qui (e una riga in
`ALLOWED_SCENARIOS` nel backend).

### `selectScenario(key)`
Imposta la card come selezionata, scrive i default nei campi.
Rifiuta la selezione se un run è in corso (mostra notifica).

### `startTest()`
Costruisce il payload JSON con scenario+parametri+host e fa
`fetch(POST /start-test)`. Gestisce tutti gli errori HTTP (409, 400,
422) mostrando la causa nella notifica toast.

### `stopTest()`
`fetch(POST /stop-test)`. Aggiorna immediatamente l'UI a stato idle
per feedback istantaneo (lo stato reale viene confermato dal prossimo
poll).

### `pollStatus()`
Chiamato ogni 2 s. Tre compiti:

1. Aggiorna l'indicatore "launcher online/offline";
2. Sincronizza lo stato running/idle (recuperabile anche dopo un
   refresh della pagina, mid-run);
3. Accoda i nuovi log al pannello (dedup per timestamp).

### `appendLogs(entries)`
Riceve un array di nuove righe e le accoda al DOM. Auto-scroll al
fondo, aggiorna il contatore. Le righe più vecchie restano: la pagina
non ha limite (a differenza del backend, che ha un ring buffer da
1000).

### `setRunningUI(running)`
Toggle centralizzato di tutti gli elementi UI che cambiano stato:
bottoni, campi (lock/unlock), pill di stato, header. Tenerlo in un
solo punto evita inconsistenze.

## Pattern usati

- **Single Page Application senza framework.** Per un controller
  semplice come questo, vanilla JS è perfetto: tempo di startup
  nullo, debug immediato, niente toolchain.
- **Data-driven rendering.** `renderScenarioGrid()` itera l'array
  `SCENARIOS` e produce DOM. Cambiare i dati → cambia la UI.
- **Polling vs WebSocket.** Polling ogni 2 s è "good enough" per
  questo use case (run da decine di secondi a minuti). Una WebSocket
  porterebbe complessità (riconnessioni, gestione errori) senza
  benefici proporzionati.
- **Optimistic UI.** Su Stop, l'UI passa subito a "idle"; il poll
  successivo conferma. Evita la sensazione di lag.
- **Design system shared.** Stesse variabili CSS, stessi font, stessa
  topbar di `dashboard.html`. Le due pagine si percepiscono come parti
  di uno stesso prodotto, non come due strumenti separati.

## Dipendenze esterne

- **Google Fonts** (`Instrument Sans` + `DM Mono`). Caricati via
  `<link>` dal CDN. La pagina funziona anche offline (fallback ai
  font di sistema), solo con tipografia diversa.

Nessun altro asset esterno.

## Limiti noti

- **Niente persistenza.** Refresh della pagina perde lo stato dei
  campi (ma non lo stato del run, che vive nel backend e viene
  recuperato dal primo `/status`).
- **Niente storico dei test.** Solo il run corrente; quelli passati
  non sono salvati. Per uno storico servirebbe persistenza nel
  launcher.
- **Niente metriche.** Latenza, percentili, throughput vengono mostrati
  da Locust nei log, ma non graficati. La dashboard separata
  (`dashboard.html`) è la sede prevista per i grafici.
