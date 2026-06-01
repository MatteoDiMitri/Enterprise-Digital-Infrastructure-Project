# `launcher/main.py`

App **FastAPI** che espone gli endpoint REST con cui la dashboard HTML
controlla i test Locust. È il "centralino": riceve richieste dal
browser, valida i parametri, delega l'esecuzione a `runner.py`.

# Panoramica Architettura

## Come funziona nel suo insieme

Il file è volutamente **piccolo e dichiarativo**: ~100 righe di codice
utile. Tutta la complessità (gestire un sottoprocesso, leggerne lo
stdout, classificare i log) sta nel `LocustRunner` in `runner.py`.
Questo file è solo la "buccia HTTP" attorno al runner.

```
┌────────────────────────────────────────────────────────┐
│  main.py                                               │
│                                                        │
│   FastAPI app                                          │
│      │                                                 │
│      ├── POST /start-test  ──┐                         │
│      ├── POST /stop-test   ──┤                         │
│      ├── GET  /status      ──┼─►  LocustRunner         │
│      └── GET  /            ──┘   (singleton)           │
│                                                        │
│   StartRequest (Pydantic)                              │
│   ALLOWED_SCENARIOS  (whitelist)                       │
│   CORS middleware    (permette file:// dal browser)    │
└────────────────────────────────────────────────────────┘
```

## Flusso dei dati

```
Browser
   │  POST /start-test  {scenario, users, spawn_rate, duration, host}
   ▼
StartRequest  ← Pydantic valida tipi e range (422 se non valido)
   │
   ▼
controllo whitelist
   │  scenario ∈ ALLOWED_SCENARIOS?  (400 altrimenti)
   ▼
runner.start(...)
   │  ↳ già in esecuzione?  → 409
   │  ↳ file scenario assente?  → 500
   │  ↳ ok → lancia subprocess, ritorna {status, pid, scenario, params}
   ▼
JSON di risposta al browser
```

## Componenti del modulo

### `ALLOWED_SCENARIOS` — whitelist degli scenari

```python
ALLOWED_SCENARIOS = {
    "normal", "flash_crowd", "ddos",
    "checkout_storm", "degradation", "saturation",
}
```

Set esplicito di tutti i nomi di scenario accettabili. **Non è un
glob su `scenarios/*.py`** — questo è intenzionale:

- protegge da path traversal (`"scenario": "../../etc/passwd"`);
- protegge da file lasciati per errore in `scenarios/` (un `WIP.py`
  non finito non viene esposto pubblicamente);
- forza il review umano per ogni nuovo scenario aggiunto.

### Schema `StartRequest` (Pydantic)

```python
class StartRequest(BaseModel):
    scenario:   str
    users:      int = Field(..., ge=1,   le=20_000)
    spawn_rate: int = Field(..., ge=1,   le=2_000)
    duration:   int = Field(..., ge=5,   le=3600)
    host:       Optional[str] = None
```

I vincoli `ge`/`le` proteggono da:

- valori a zero o negativi (che farebbero crashare Locust);
- valori esagerati (impedirebbero al laptop di restare reattivo);
- run troppo brevi (sotto i 5 s Locust non riesce nemmeno a spawnare gli utenti)
  o troppo lunghi (limite di sanità a 1 ora).

FastAPI restituisce automaticamente `HTTP 422` con un body che spiega
il campo problematico, senza che servano controlli manuali nel codice
della route.

### CORS middleware

```python
app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)
```

La dashboard HTML può essere aperta direttamente dal filesystem
(`file://`) o servita da un altro server. Senza CORS aperto, il
browser bloccherebbe le fetch verso `localhost:8000`.

L'apertura totale (`allow_origins=["*"]`) è sicura **solo perché il
launcher si bind a `127.0.0.1`** (vedi `README.md` di progetto):
nessuna macchina esterna può raggiungerlo.

### Gli endpoint

#### `POST /start-test`
Avvia un test. Risposte possibili:
- **200 OK** + `{status, pid, scenario, params}`
- **400 Bad Request** se lo `scenario` non è nella whitelist
- **409 Conflict** se un test è già in esecuzione
- **422 Unprocessable Entity** se i parametri Pydantic falliscono
- **500 Internal Server Error** se il file dello scenario non esiste

#### `POST /stop-test`
Ferma il test corrente (no-op se idle). Risposta:
- **200 OK** + `{status: "stopped" | "not_running"}`

#### `GET /status`
Snapshot dello stato. Chiamato ogni 2 s dalla dashboard. Risposta:
```json
{
  "running": true,
  "scenario": "flash_crowd",
  "started_at": "2026-05-22T13:42:01Z",
  "pid": 12345,
  "users": 2000, "spawn_rate": 200, "duration": 120,
  "host": "http://localhost",
  "logs": [ { "ts": "...", "level": "info", "msg": "..." }, ... ]
}
```

#### `GET /`
Endpoint diagnostico. Ritorna `{name, scenarios, default_host, project_dir}`.
Utile per verificare che il launcher sia raggiungibile prima di
provare a usare `/start-test`.

### Singleton `runner`

```python
runner = LocustRunner(project_dir=PROJECT_DIR, default_host=DEFAULT_HOST)
```

Istanziato una volta a livello modulo. Tutte le route condividono
questa istanza (e quindi lo stesso lock, lo stesso log buffer, lo
stesso processo). È la conseguenza naturale del fatto che un laptop
può ragionevolmente ospitare un solo Locust alla volta.

## Pattern usati

- **Layered architecture.** Validazione → routing → delega → risposta.
  Ogni layer ha una sola responsabilità.
- **DTO con Pydantic.** Il contratto HTTP è dichiarativo, non
  imperativo. Aggiungere un campo richiede una sola riga.
- **Singleton del runner.** Il vincolo "un solo Locust alla volta"
  è esplicito e cementato nel codice tramite il modulo-livello del
  runner e l'HTTP 409 al doppio-start.
- **Mappa dei codici di errore.** Ogni eccezione del runner viene
  tradotta in un HTTP status semanticamente corretto (`RuntimeError`
  → 409, `FileNotFoundError` → 500), così il client può reagire
  correttamente.

## Configurazione

| Variabile               | Default              | Effetto                              |
| ----------------------- | -------------------- | ------------------------------------ |
| `LOCUST_TARGET_HOST`    | `http://localhost`   | Host di default se assente nel body  |

## Esecuzione

```bash
uvicorn launcher.main:app --host 127.0.0.1 --port 8000 --reload
```

- `--host 127.0.0.1` è importante: l'apertura totale del CORS è
  sicura solo se il servizio non è esposto in rete.
- `--reload` ricarica automaticamente su modifica del codice (utile
  in sviluppo, da rimuovere in produzione).

## Dipendenze

```
fastapi          (FastAPI, HTTPException)
pydantic         (BaseModel, Field)
fastapi.middleware.cors  (CORSMiddleware)
launcher.runner  (LocustRunner)
```
