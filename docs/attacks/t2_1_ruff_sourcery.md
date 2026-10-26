# T2.1: regole Ruff equivalenti alle regole Sourcery di ACW

Tabella di corrispondenza per la parte Python di T2.1 (D35, ADR-014; decisione dell'utente del 26
ottobre 2026). Lo SPEC (§12) chiede "le regole Ruff estese usate da ACW", ma ACW usa **Sourcery**
(`third_party/ACW/source/refact_list.py`, audit di ACW §2): T2.1 usa le regole di **Ruff 0.7.4**
(versione di `bench-core`) equivalenti alle 35 regole Sourcery dell'elenco.

Criteri:

- "equivalente" = la regola Ruff riconosce lo stesso schema e la sua correzione produce la stessa
  trasformazione (o quasi: differenze indicate in nota);
- una regola Ruff senza correzione automatica nella 0.7.4 conta come **nessun equivalente** (T2.1
  deve trasformare il codice);
- molte correzioni sono "unsafe" o la regola è in preview nella 0.7.4: T2.1 le applica con
  `--unsafe-fixes --preview` sulle sole regole della tabella (verifica sulle canoniche e sulla
  baseline di L1 dev prima del lancio);
- le regole 5 e 10 sono nell'elenco ma **fuori dal pool di ACW** (il codice le toglie): non entrano
  nell'attacco.

Corrispondenze provate sulla 0.7.4 (`ruff rule <codice>` e applicazione su un esempio per regola).

| # | Regola Sourcery (ACW) | Regola Ruff 0.7.4 | Correzione | Tipo T2.1 |
|---|---|---|---|---|
| 1 | remove-unnecessary-else | RET505, RET506, RET507, RET508 | sicura per i casi semplici, altrimenti unsafe | `ruff:remove-unnecessary-else` |
| 2 | hoist-statement-from-loop | nessun equivalente | — | — |
| 3 | boolean-if-exp-identity | SIM210, SIM211 | unsafe | `ruff:boolean-if-exp-identity` |
| 4 | list-comprehension | PERF401 | unsafe, solo con `--preview` | `ruff:list-comprehension` |
| 5 | remove-unnecessary-cast | UP018, C414 (parziale) | — | fuori dal pool di ACW: esclusa |
| 6 | merge-comparisons | PLR1714 | unsafe | `ruff:merge-comparisons` |
| 7 | sum-comprehension | nessun equivalente | — | — |
| 8 | merge-list-append | nessun equivalente (FURB113 riguarda append consecutivi, non lista vuota + append) | — | — |
| 9 | merge-nested-ifs | SIM102 | unsafe | `ruff:merge-nested-ifs` |
| 10 | avoid-builtin-shadow | A001, A002 (senza correzione) | — | fuori dal pool di ACW: esclusa |
| 11 | inline-immediately-returned-variable | RET504 | unsafe | `ruff:inline-immediately-returned-variable` |
| 12 | for-index-underscore | B007 (rinomina in `_i`, Sourcery in `_`) | unsafe | `ruff:for-index-underscore` |
| 13 | simplify-constant-sum | nessun equivalente | — | — |
| 14 | simplify-len-comparison | nessun equivalente nella 0.7.4 (PLC1802 arriva dopo) | — | — |
| 15 | comprehension-to-generator | C419 | unsafe | `ruff:comprehension-to-generator` |
| 16 | use-any | SIM110 | unsafe | `ruff:use-any` |
| 17 | reintroduce-else | nessun equivalente | — | — |
| 18 | min-max-identity | FURB136 | sicura | `ruff:min-max-identity` |
| 19 | merge-duplicate-blocks | SIM114 | sicura | `ruff:merge-duplicate-blocks` |
| 20 | square-identity | nessun equivalente | — | — |
| 21 | move-assign-in-block | nessun equivalente | — | — |
| 22 | merge-else-if-into-elif | PLR5501 | sicura | `ruff:merge-else-if-into-elif` |
| 23 | use-next | nessun equivalente | — | — |
| 24 | swap-if-else-branches | nessun equivalente in Ruff (affine al tipo AST `if_else_inversion`, non uguale: Sourcery inverte solo con un ramo `pass`) | — | — |
| 25 | switch | nessun equivalente | — | — |
| 26 | invert-any-all | nessun equivalente | — | — |
| 27 | remove-redundant-if | nessun equivalente | — | — |
| 28 | use-join | nessun equivalente | — | — |
| 29 | for-append-to-extend | PERF401 (stessa regola della 4) | unsafe, solo con `--preview` | `ruff:list-comprehension` |
| 30 | hoist-similar-statement-from-if | nessun equivalente | — | — |
| 31 | identity-comprehension | C416 | unsafe | `ruff:identity-comprehension` |
| 32 | merge-list-appends-into-extend | FURB113 | unsafe, regola in preview | `ruff:merge-list-appends-into-extend` |
| 33 | use-dict-items | nessun equivalente (PLC0206 senza correzione) | — | — |
| 34 | remove-redundant-slice-index | nessun equivalente | — | — |
| 35 | assign-if-exp | SIM108 | unsafe | `ruff:assign-if-exp` |

**Copertura:** 16 delle 33 regole Sourcery che ACW usa hanno un equivalente in Ruff 0.7.4 con
correzione automatica (1, 3, 4, 6, 9, 11, 12, 15, 16, 18, 19, 22, 29, 31, 32, 35), con 15 tipi
T2.1 (4 e 29 condividono PERF401); 17 non ne hanno. Sulle 35 dell'elenco, 17 hanno un equivalente
(anche la 5, esclusa perché fuori dal pool).

**Sovrapposizione con l'inserimento di ACW:** oltre ai 15 tipi Ruff, due tipi del catalogo AST
corrispondono ai riordini di ACW (libcst, `base_ast.py`): `commutative_swap` su `*` (regola 37) e
`comparison_flip` (38 per `<`/`>`, 39 per `<=`/`>=`). Il report di T2.1 per ACW riporta la quota
dei tipi applicati equivalenti a una regola della configurazione congelata (30 regole): su ACW
l'attacco è in parte mirato, e va dichiarato nella tesi accanto ai suoi risultati.
