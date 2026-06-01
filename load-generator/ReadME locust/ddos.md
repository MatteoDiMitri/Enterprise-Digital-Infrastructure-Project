# `scenarios/ddos.py`

Scenario **DDoS-like / flood controllato**. Stressa il livello di rete
e il front-end del server con un alto rate di richieste GET.

> ⚠️ Questo scenario è uno **strumento di load testing educativo**, da
> usare **esclusivamente** contro infrastruttura di cui si possiede
> autorizzazione esplicita ai test (la propria macchina di sviluppo o
> un server di laboratorio). Non contiene alcun comportamento
> realmente malevolo (no payload offensivi, no amplificazione, no
> spoofing della sorgente).

# Panoramica Architettura

## Come funziona nel suo insieme

Lo scopo non è simulare un attacco vero, ma misurare **quanto
sostiene il server al livello di richieste/secondo**, ignorando la
logica applicativa.

Per questo lo scenario:

- usa solo `GET /` (la home page), endpoint più leggero;
- non esegue mai POST (niente checkout, niente scritture sul DB);
- ha think-time ridotto al minimo (50–200 ms);
- non eredita da `ShopUser`, per evitare di trascinarsi dietro task di
  ordine.

Questo isola il test al solo livello di rete / web server / PHP-FPM
front-end, senza coinvolgere database o logiche di business.

## Flusso dei dati

```
Locust → istanzia N istanze di DDoSUser
       → ogni utente cicla:
           ├── @task flood_home → GET /
           └── wait_time random 50–200 ms
       → 1000+ richieste/secondo aggregate
```

## Componenti del modulo

### Classe `DDoSUser(HttpUser)`

Eredita **direttamente da `HttpUser`**, *non* da `ShopUser`. Questa è
una decisione intenzionale: ereditare dalla base del negozio
rischierebbe di portarsi dentro `task_order` come task pesato — un
flood che riempie il database di ordini falsi rovinerebbe lo scopo del
test.

```python
class DDoSUser(HttpUser):
    wait_time = between(0.05, 0.2)

    @task
    def flood_home(self):
        self.client.get("/", name="GET / (flood)")
```

- Il `name=` esplicito assicura che le statistiche raggruppino tutte
  le richieste sotto una singola voce nel report Locust.
- Il think-time minimo evita di degenerare in un *busy loop* puro:
  per quello esiste lo scenario `saturation.py`.

## Perché non zero think-time

Con `wait_time = between(0, 0)` (o senza dichiararlo) Locust
spedirebbe richieste a *vuoto* il più velocemente possibile —
comportamento utile in alcuni test, ma più "pulito" per misurare la
capacità massima (è quello che fa `saturation.py`). Qui invece teniamo
un floor di 50 ms per simulare un pattern di flood "umano" con tante
sorgenti distribuite.

## Pattern usati

- **Composition over inheritance.** Non si eredita da `ShopUser` solo
  perché "sembra simile": il ruolo di questo scenario è diverso, e
  l'ereditarietà sbagliata sarebbe un bug latente.
- **Endpoint minimo.** Ridurre la superficie d'attacco del test a un
  solo URL massimizza l'isolamento della variabile misurata.

## Esempio di esecuzione CLI

```bash
locust -f scenarios/ddos.py --headless \
       -u 1500 -r 300 -t 90s \
       --host http://localhost
```

## Dipendenze

```
locust  (HttpUser, between, task)
```
