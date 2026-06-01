# `requirements.txt`

Elenco delle dipendenze Python del progetto, installabili con
`pip install -r requirements.txt`.

# Panoramica Architettura

## Come funziona nel suo insieme

Il file segue il formato standard di `pip`: una dipendenza per riga,
con vincolo di versione minimo (`>=`).

```
locust>=2.20.0
fastapi>=0.110.0
uvicorn[standard]>=0.27.0
```

## Ruolo delle tre dipendenze

### `locust>=2.20.0`
Motore di load testing. Genera utenti virtuali, gestisce il loro
ciclo di vita, raccoglie le statistiche. Il vincolo `>=2.20.0`
garantisce la presenza di:

- `LoadTestShape` con il metodo `tick()` moderno (usato in
  `flash_crowd.py`);
- `catch_response=True` su tutti i tipi di richiesta (usato in
  `_base.py` e `degradation.py`);
- l'attributo `abstract = True` per le classi base.

### `fastapi>=0.110.0`
Framework HTTP del launcher. Versione recente per:

- supporto Pydantic v2 (validazione tipi più rigorosa e veloce);
- migliorie su `HTTPException` e i middleware CORS;
- corretto pattern matching dei path parameter.

### `uvicorn[standard]>=0.27.0`
ASGI server che esegue l'app FastAPI. Il modificatore `[standard]`
include dipendenze opzionali utili in sviluppo (auto-reload migliore,
parsing HTTP più veloce con `httptools`).

## Cosa NON c'è (e perché)

- **Database driver.** Il launcher non persiste stato.
- **Job queue (Celery, RQ, ecc).** Un solo Locust alla volta, niente
  concorrenza da orchestrare.
- **Frontend bundler (npm, webpack, vite).** La dashboard è HTML+CSS+JS
  puro, nessuna build step.

Questa **minimalità è intenzionale**: meno dipendenze significa meno
superficie di vulnerabilità, meno conflitti di versione, e meno
attrito per chi clona il repo.

## Pattern usati

- **Vincolo di versione "lower bound" (`>=`).** Non blocchiamo a una
  versione esatta perché:
  - il progetto è didattico, non deve essere bit-reproducibile su
    pipeline CI;
  - le librerie usate sono mature e con buone garanzie di backward
    compatibility nel patch range.

- **Nessun `requirements-dev.txt` separato.** Il progetto non ha
  test automatici da eseguire in CI. Se in futuro si aggiungono
  `pytest`, `black`, `ruff`, andrebbero in un secondo file.

## Installazione

```bash
python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Dipendenze transitive notevoli

Quando installi le tre dipendenze, pip tira dentro anche:

| Pacchetto      | Da chi  | Ruolo                              |
| -------------- | ------- | ---------------------------------- |
| `gevent`       | locust  | Greenlets per simulare migliaia di utenti |
| `pydantic`     | fastapi | Validazione dei modelli            |
| `starlette`    | fastapi | Routing e middleware sottostanti   |
| `httptools`    | uvicorn[standard] | Parser HTTP veloce in C  |
| `websockets`   | uvicorn[standard] | Supporto WebSocket       |

Non vanno aggiunte manualmente al file: pip risolve da sé.
