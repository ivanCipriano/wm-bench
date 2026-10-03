# ADR-005 — Versioni fissate delle grammatiche tree-sitter in bench-core

- **Stato:** accettata (Milestone 2, 3 ottobre 2026; decisione dell'utente)
- **Contesto:** SPEC §20.1 (tree-sitter alle versioni richieste da `codebleu==0.7.0`, una sola versione nell'ambiente), lockfile della M0.

## Problema

Il lockfile della M0 contiene `tree-sitter==0.22.3` (codebleu 0.7.0 richiede `tree-sitter>=0.22,<0.23`) con le grammatiche più recenti consentite dal vincolo `~=0.21` di codebleu: `tree-sitter-python==0.25.0`, `tree-sitter-java==0.23.5`, `tree-sitter-cpp==0.23.4`, `tree-sitter-javascript==0.25.0`. Questa combinazione **non funziona**:

- dalla 0.23 le grammatiche restituiscono da `language()` un `PyCapsule` e non un intero, e `tree_sitter.Language(...)` della 0.22.3 fallisce con `TypeError: an integer is required`. Il problema colpisce anche codebleu, che usa proprio `Language(tree_sitter_<lang>.language())`;
- `tree-sitter-python` 0.25 e `tree-sitter-javascript` 0.25 hanno inoltre ABI 15, mentre la 0.22.3 accetta solo ABI 13–14 (`Incompatible Language version 15`).

`pip check` non se ne accorge: i vincoli dichiarati sono soddisfatti.

## Decisione

Si fissano in `environment-bench-core.yml` le versioni più recenti compatibili con `tree-sitter==0.22.3` e con i vincoli di codebleu:

| Pacchetto | Versione | ABI |
|---|---|---|
| tree-sitter | 0.22.3 (invariato) | supporta 13–14 |
| tree-sitter-python | 0.21.0 | 14 |
| tree-sitter-java | 0.21.0 | 14 |
| tree-sitter-cpp | 0.22.3 | 14 |
| tree-sitter-javascript | 0.21.4 | 14 |

codebleu resta 0.7.0. Il resto dell'ambiente non cambia. Le grammatiche degli altri linguaggi installate da `codebleu[all]` (C, C#, Go, PHP, Ruby, Rust) non si usano e non vengono toccate.

Verificato in un ambiente locale con le stesse versioni: parsing senza nodi ERROR e CodeBLEU (con `syntax_match` e `dataflow_match` non nulli) sui 4 linguaggi.

Non è una deviazione dal protocollo sperimentale, ma una scelta di implementazione: niente voce in `docs/deviations.md`.

## Conseguenze

- `tests/unit/test_treesitter_regression.py` controlla, a ogni esecuzione della suite e anche sul cluster:
  - che le versioni installate coincidano con quelle fissate nel file dell'ambiente;
  - che l'ABI sia ≤ 14;
  - che 4 frammenti validi si analizzino senza errori;
  - che CodeBLEU giri su una coppia di esempio per linguaggio.
- I manifest registrano le versioni di tree-sitter e delle 4 grammatiche (`provenance.parser_versions`), perché i conteggi di righe e di nodi dipendono da esse.
- I lockfile `requirements-bench-core.lock` e `conda-bench-core.lock` vanno rigenerati sul cluster dopo l'installazione (comandi nel riepilogo della M2).
