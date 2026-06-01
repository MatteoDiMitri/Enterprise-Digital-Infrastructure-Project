# `scenarios/_base.py`

Modulo base condiviso da tutti gli scenari Locust del progetto. Definisce
**la classe astratta `ShopUser`** che incapsula il *journey* di un
visitatore tipico del negozio PHP e il catalogo prodotti usato per
generare carrelli realistici.

# Panoramica Architettura

## Come funziona nel suo insieme

Questo file è la **base ereditata da tutti gli scenari concreti**
(`normal.py`, `flash_crowd.py`, `checkout_storm.py`, `degradation.py`,
`saturation.py`). L'idea è semplice: il *comportamento* di un utente
del negozio (quali endpoint chiama, in che ordine, con quale payload)
è uguale in tutti gli scenari. Quello che cambia è *con quale ritmo*,
*con quali pesi sui task*, e *con quale forma del carico* nel tempo.

`_base.py` codifica una sola volta il comportamento condiviso. Gli
altri scenari ereditano e personalizzano solo i parametri.

## Flusso dei dati

Quando un utente virtuale viene istanziato da Locust:

1. **`on_start()`** scatta una sola volta per utente: simula
   l'atterraggio sulla home page (`GET /`).
2. Locust sceglie ripetutamente un task dal dizionario `tasks` della
   sottoclasse, rispettando i pesi.
3. Il task scelto esegue una chiamata HTTP. Per le richieste con
   `catch_response`, la risposta viene **ispezionata nel corpo
   JSON** prima di essere classificata come successo o fallimento.
4. Tra un task e l'altro Locust attende un `wait_time` casuale
   (di default 1–5 s).

## Componenti del modulo

### Catalogo prodotti — `PRODUCT_CATALOG`

Lista statica di 8 prodotti con `id` e `price`. Serve a due scopi:
- estrarre prodotti randomici per simulare la navigazione di dettaglio;
- generare carrelli plausibili da inviare a `POST /checkout.php`.

I prezzi vivono lato client solo per il payload — il file PHP
originale (`checkout.php`) **ricalcola sempre i totali server-side**,
quindi qui non c'è rischio di "fidarsi del client". È una scelta
documentata anche nei commenti del file.

### Funzione `random_cart()`

Costruisce un payload `{items: [{id, qty, price}, …]}` con 1–4 prodotti
estratti senza ripetizione, quantità casuale 1–3. Il formato rispetta
**esattamente** lo schema atteso da `checkout.php`.

### Classe astratta `ShopUser`

```python
class ShopUser(HttpUser):
    abstract = True
    wait_time = between(1, 5)
    SLOW_THRESHOLD_MS = None
```

- **`abstract = True`** è un attributo Locust che impedisce di
  istanziare questa classe direttamente: viene saltata dalla
  *discovery* automatica anche se il file viene caricato per errore.
- **`wait_time`** rispetta il requisito del progetto (1–5 secondi).
  Le sottoclassi che vogliono un ritmo diverso (DDoS, Saturation) lo
  ridefiniscono.
- **`SLOW_THRESHOLD_MS`** è opt-in: quando una sottoclasse lo imposta,
  ogni risposta più lenta di quella soglia viene classificata come
  *failure* (vedi `_mark_slow_if_needed`). Usato da `degradation.py`.

### I tre task del journey

| Metodo            | HTTP                              | Note                                              |
| ----------------- | --------------------------------- | ------------------------------------------------- |
| `task_browse()`   | `GET /index.php`                  | Più frequente, simula lo sfogliare i prodotti     |
| `task_detail()`   | `GET /index.php?product_id=[id]`  | Apre una scheda prodotto                          |
| `task_order()`    | `POST /checkout.php` (JSON)       | Verifica HTTP code **e** `success:true` nel JSON  |

Il `name=` esplicito su ogni `client.get/post` raggruppa correttamente
le statistiche nei report Locust: senza di esso, ogni URL parametrizzato
sarebbe contato come endpoint separato.

### Validazione semantica in `task_order`

Il checkout PHP risponde sempre con HTTP 200 anche in caso di errore
logico (carrello vuoto, errore DB, ecc.). Per questo motivo
`task_order` legge il JSON e marca la richiesta come fallita se
`success` è `false`. Questo evita falsi positivi nei report.

## Perché `tasks` NON è definito qui

Locust ha una *metaclasse* che **fonde** automaticamente il dizionario
`tasks` ereditato con quello della sottoclasse. Se la base avesse il
suo `tasks`, ogni scenario figlio ne raddoppierebbe i pesi
silenziosamente. Per questo il file lascia a ogni scenario concreto la
responsabilità di dichiarare i suoi pesi da zero.

Questa è una scelta architetturale documentata nel commento del codice
e fissata da un bug reale incontrato durante lo sviluppo.

## Pattern usati

- **Template Method.** La base definisce gli step del journey
  (`task_browse`, `task_detail`, `task_order`); le sottoclassi
  riusano questi step combinandoli con pesi diversi.
- **Strategy (parametri di classe).** `wait_time` e `SLOW_THRESHOLD_MS`
  sono *attributi di classe*, sostituibili nelle sottoclassi senza
  toccare il codice del journey.
- **Abstract Base Class.** `abstract = True` impedisce l'istanziazione
  diretta — è una guardia esplicita, non una convenzione.

## Dipendenze

```
locust  (HttpUser, between)
random  (stdlib)
```

## Estensione

Per aggiungere un nuovo task al journey condiviso (esempio: una
ricerca prodotti):

1. Aggiungere il metodo `task_search()` in `ShopUser`.
2. Inserirlo nel dizionario `tasks` di ogni scenario che lo desidera.

Per aggiungere uno scenario nuovo che riusi il journey, vedere
`scenarios/normal.py` come esempio minimale.
