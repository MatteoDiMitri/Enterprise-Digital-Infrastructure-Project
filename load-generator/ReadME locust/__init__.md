# `launcher/__init__.py`

File **vuoto**. La sua sola funzione è dichiarare `launcher/` come
package Python.

# Panoramica Architettura

## Come funziona nel suo insieme

Senza questo file, Python prima della versione 3.3 non avrebbe
riconosciuto `launcher/` come package importabile. Dalla 3.3 in poi
esistono i *namespace packages* impliciti (senza `__init__.py`), ma
mantenere il file vuoto:

- è una convenzione esplicita e immediatamente riconoscibile;
- evita ambiguità con strumenti di pacchettizzazione (setuptools,
  poetry, pip);
- permette import del tipo `from launcher.runner import LocustRunner`
  in modo non ambiguo.

## Quando contiene logica

In progetti più grandi, `__init__.py` può:

- riesportare simboli a livello di package
  (`from .runner import LocustRunner` per fare poi
  `from launcher import LocustRunner`);
- definire `__all__` per limitare gli import wildcard;
- contenere codice di inizializzazione lazy.

In questo progetto **non serve nulla di tutto questo**: i due
moduli (`main.py` e `runner.py`) si importano già esplicitamente.
Tenere il file vuoto comunica chiaramente al lettore: "è un package,
e non c'è alcuna magia di inizializzazione da cercare qui".

## Pattern usati

- **Convention over configuration.** Lasciare il file vuoto è il
  modo standard di dire "package senza side effects".

## Dipendenze

Nessuna.
