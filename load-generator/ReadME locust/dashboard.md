# `dashboard.html`

Dashboard di **osservabilità** del sistema sotto test. Mostra KPI,
grafici e log di servizio. È la pagina "di visualizzazione" del
progetto, contrapposta a `control_panel.html` (la pagina "di
controllo").

# Panoramica Architettura

## Come funziona nel suo insieme

Single-page application statica, stesso stack di `control_panel.html`
(HTML + CSS + JS vanilla). Diversamente da quella, **non comanda
nulla**: si limita a leggere lo stato del sistema e a visualizzarlo.

In origine la pagina conteneva anche una sidebar con i pulsanti per
lanciare gli scenari. Quei pulsanti sono stati rimossi: l'orchestrazione
ora vive in `control_panel.html`, e dashboard.html è dedicata
puramente alla visualizzazione.

## Sezioni della pagina

### Topbar
Identica a `control_panel.html`: logo, breadcrumb, nav (Shop / Team /
Control Panel / Dashboard), pill di stato, orologio. La coerenza
visiva è voluta — il sistema deve sembrare un prodotto unico.

### Banner "Active scenario"
Comparsa solo quando il backend riporta uno scenario in corso. Mostra
icona, descrizione, timer elapsed. Utile per chi guarda i grafici
senza essere passato per la control panel.

### KPI grid
Quattro indicatori sintetici della salute del sistema (richieste/sec,
latenza p99, error rate, utenti attivi). Aggiornati a polling.

### Charts row
Grafici di andamento nel tempo. Tipicamente:

- request rate e error rate sovrapposti;
- latenza p50/p95/p99 su scala logaritmica;
- distribuzione del traffico per endpoint.

### Topology grid
Diagramma dei servizi e del loro stato in tempo reale (nginx, php-fpm,
mysql, ecc.). Ogni nodo ha un indicatore di salute (verde/giallo/rosso).

### Logs
Log di servizio (non i log di Locust, che vivono in control_panel).
Sono i messaggi che il sistema sotto test produce durante l'esecuzione,
classificati per livello e filtrabili.

## Differenze chiave con `control_panel.html`

| Aspetto                       | `control_panel.html`           | `dashboard.html`               |
| ----------------------------- | ------------------------------ | ------------------------------ |
| Ruolo                         | Lancia i test                  | Visualizza il sistema          |
| Endpoint chiamati             | `/start-test`, `/stop-test`    | API di metriche/log            |
| Direzione del dato            | Browser → backend (comandi)    | Backend → browser (lettura)    |
| Stato editabile               | Sì (form, bottoni)             | No (read-only)                 |

## Pattern usati

- **Read-only view.** Nessun input dell'utente influenza lo stato del
  sistema: la pagina osserva soltanto. Questo elimina classi intere di
  bug (race condition fra azione e visualizzazione).
- **Polling sincronizzato.** Tutti i grafici si aggiornano allo stesso
  intervallo di poll, evitando inconsistenze temporali fra widget.
- **Design system condiviso** con `control_panel.html` (stesse
  variabili CSS, stessi componenti). Le due pagine vivono nello stesso
  prodotto.

## Connessione con il resto del progetto

Nel flusso completo:

```
control_panel.html  ─►  launcher  ─►  locust  ─►  PHP shop
                                                       │
                                                       │  emette metriche/log
                                                       ▼
                                              (Prometheus / Grafana — futuro)
                                                       │
                                                       ▼
                                              dashboard.html (osservazione)
```

`dashboard.html` è il punto di osservazione di tutto questo flusso
dal lato del sistema sotto test. Il control panel è il punto di
controllo dal lato del generatore di carico.

## Modifiche storiche

- **Sidebar rimossa.** La pagina originaria conteneva una sidebar di
  600px con 6 card di scenario e bottoni Run/Stop. È stata estratta in
  `control_panel.html` per separare responsabilità.
- **Layout cambiato.** Da grid `240px 1fr` a singola colonna centrata
  (max-width 1600px).
- **Nav aggiornata.** Aggiunto il link "Control Panel" nel topbar per
  navigazione fra le due pagine.

## Dipendenze esterne

Stesse di `control_panel.html`: Google Fonts (`Instrument Sans`,
`DM Mono`). Nessuna libreria JavaScript esterna.
