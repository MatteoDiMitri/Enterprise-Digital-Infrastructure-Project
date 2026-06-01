# `locustfile.py`

Entry-point CLI di default per Locust. Permette di lanciare il progetto
con un semplice `locust` dalla cartella radice, senza specificare
`-f scenarios/<nome>.py`.

# Panoramica Architettura

## Come funziona nel suo insieme

Per convenzione, quando Locust viene invocato senza il flag `-f` cerca
un file chiamato `locustfile.py` nella directory corrente e usa quello.
Questo file fa esattamente questo: importa `NormalUser` dallo scenario
baseline e lo espone come comportamento di default.

```bash
# senza il file:
locust -f scenarios/normal.py --host http://localhost

# con questo file:
locust --host http://localhost     # equivalente
```

È **convenienza pura per la CLI**. Il launcher FastAPI passa sempre
`-f scenarios/<nome>.py` esplicito, quindi non interagisce mai con
questo file.

## Flusso dei dati

```
$ locust --host …
   │
   ▼
Locust cerca ./locustfile.py
   │
   ▼
locustfile.py:
   ├── aggiunge scenarios/ a sys.path
   └── from normal import NormalUser
   │
   ▼
Locust scopre NormalUser e lo avvia
```

## Componenti del modulo

### Manipolazione di `sys.path`

```python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scenarios"))
```

Il motivo: quando Locust carica un file con `-f`, aggiunge la
*directory di quel file* al `sys.path`. I file in `scenarios/` si
importano fra loro con `from _base import ShopUser` (path relativo).
Ma quando il file caricato è `locustfile.py` (alla radice), la
directory aggiunta da Locust è la root, non `scenarios/` — e gli
import relativi falliscono.

L'inserimento esplicito di `scenarios/` in `sys.path` risolve il
problema senza dover trasformare `scenarios/` in un vero package
Python con `__init__.py`.

### Import dello scenario di default

```python
from normal import NormalUser  # noqa: F401
```

Locust scopre i `User` per introspezione del modulo caricato — basta
che `NormalUser` sia presente nel namespace. Il commento `noqa: F401`
silenzia il warning del linter per gli import inutilizzati.

## Pattern usati

- **Convention over configuration.** Sfrutta la convenzione Locust
  per ridurre il comando da `locust -f scenarios/normal.py …` a
  `locust …`.
- **Façade.** Espone allo strumento esterno (Locust CLI) un punto di
  ingresso unico nascondendo la struttura interna in sottocartelle.

## Quando viene usato (e quando no)

| Contesto                                            | Usa `locustfile.py`? |
| --------------------------------------------------- | -------------------- |
| `locust` lanciato a mano dalla root                 | ✅ sì                 |
| `locust -f scenarios/<nome>.py` esplicito           | ❌ no, viene ignorato |
| Launcher FastAPI (`runner.py` → `subprocess`)       | ❌ no, sempre `-f`    |
| UI Web di Locust con scenario di default            | ✅ sì                 |

## Dipendenze

```
sys, os      (stdlib)
scenarios/normal.py
scenarios/_base.py
```
